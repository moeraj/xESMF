'''
Frontend for xESMF, exposed to users.
'''

import numpy as np
import xarray as xr
import os

from . backend import (esmf_grid, add_corner,
                       esmf_regrid_build, esmf_regrid_finalize)

from . smm import read_weights, apply_weights


def as_2d_mesh(lon, lat):

    if (lon.ndim, lat.ndim) == (2, 2):
        pass
    elif (lon.ndim, lat.ndim) == (1, 1):
        lon, lat = np.meshgrid(lon, lat)
    else:
        raise ValueError('lon and lat should be both 1D or 2D')

    return lon, lat

def ds_to_ESMFgrid(ds, need_bounds=False, periodic=None):
    '''
    Convert xarray DataSet or dictionary to ESMF.Grid object.

    Parameters
    ----------
    ds : xarray DataSet or dictionary
        Contains variables ``lon``, ``lat``,
        and optionally ``lon_b``, ``lat_b`` if need_bounds=True.

        Shape should be ``(Nlat, Nlon)`` or ``(Ny, Nx)``,
        as normal C or Python ordering. Will be then tranposed to F-ordered.

    need_bounds : bool, optional
        Need cell boundary values?

    periodic : bool, optional
        Periodic in longitude?

    Returns
    -------
    grid : ESMF.Grid object

    '''

    # use np.asarray(dr) instead of dr.values, so it also works for dictionary
    lon = np.asarray(ds['lon'])
    lat = np.asarray(ds['lat'])

    # tranpose the arrays so they become Fortran-ordered
    grid = esmf_grid(lon.T, lat.T, periodic=periodic)

    if need_bounds:
        lon_b = np.asarray(ds['lon_b'])
        lat_b = np.asarray(ds['lat_b'])
        add_corner(grid, lon_b.T, lat_b.T)

    return grid


class Regridder(object):
    def __init__(self, ds_in, ds_out, method, periodic=False,
                 filename=None, reuse_weights=False):
        """
        Make xESMF regridder

        Parameters
        ----------
        ds_in, ds_out : xarray DataSet, or dictionary
            Contain input and output grid coordinates. Look for variables
            'lon', 'lat', and optionally 'lon_b', 'lat_b' for conservative
            method.

        method : str, optional
            Regridding method. Options are
            - 'bilinear'
            - 'conservative', need grid corner information
            - 'patch'
            - 'nearest_s2d'
            - 'nearest_d2s'

        periodic : bool, optional
            Periodic in longitude? Default to False.
            Only useful for global grids with non-conservative regridding.
            Will be forced to False for conservative regridding.

        filename : str, optional
            Name for the weight file. The default naming scheme is
            {method}_{Ny_in}x{Nx_in}_{Ny_out}x{Nx_out}.nc,
            e.g. bilinear_400x600_300x400.nc

        reuse_weights : bool, optional
            Whether to read existing weight file to save computing time.
            False by default (i.e. re-compute, not reuse).

        Returns
        -------
        regridder : xESMF regridder object

        """

        # record output grid coordinate, to be added to regridding results
        self._lon_out = np.asarray(ds_out['lon'])
        self._lat_out = np.asarray(ds_out['lat'])

        # record basic switches
        if method == 'conservative':
            self.need_bounds = True
            periodic = False  # bound shape will not be N+1 for periodic grid
        else:
            self.need_bounds = False

        self.method = method
        self.periodic = periodic
        self.reuse_weights = reuse_weights

        self._grid_in = ds_to_ESMFgrid(ds_in, need_bounds=self.need_bounds,
                                       periodic=periodic)
        self._grid_out = ds_to_ESMFgrid(ds_out, need_bounds=self.need_bounds)

        # get grid shape information
        # Use (Ny, Nx) instead of (Nlat, Nlon),
        # because ds can be general curvilinear grids
        # For rectilinear grids, (Ny, Nx) == (Nlat, Nlon)
        self.Ny_in, self.Nx_in = ds_in['lon'].shape
        self.Ny_out, self.Nx_out = ds_out['lon'].shape
        self.N_in = ds_in['lon'].size
        self.N_out = ds_out['lon'].size

        # only copy coordinate values, do not copy data
        # self.coords_in = ds_in.coords.to_dataset().copy()
        # self.coords_out = ds_out.coords.to_dataset().copy()

        if filename is None:
            self.filename = self._get_default_filename()
        else:
            self.filename = filename

        # get weight matrix
        self._write_weight_file()
        self.A = read_weights(self.filename, self.N_in, self.N_out)

    def _get_default_filename(self):
        # e.g. bilinear_400x600_300x400.nc
        filename = ('{0}_{1}x{2}_{3}x{4}'.format(self.method,
                    self.Ny_in, self.Nx_in,
                    self.Ny_out, self.Nx_out)
                    )
        if self.periodic:
            filename += '_peri.nc'
        else:
            filename += '.nc'

        return filename

    def _write_weight_file(self):

        if os.path.exists(self.filename):
            if self.reuse_weights:
                print('Reuse existing file: {}'.format(self.filename))
                return  # do not compute it again, just read it
            else:
                print('Overwrite existing file: {} \n'.format(self.filename),
                      'You can set reuse_weights=True to save computing time.')
                os.remove(self.filename)
        else:
            print('Create weight file: {}'.format(self.filename))

        regrid = esmf_regrid_build(self._grid_in, self._grid_out, self.method,
                                   filename=self.filename)
        esmf_regrid_finalize(regrid)  # only need weights, not regrid object

    def clean_weight_file(self):
        """
        Remove the offline weight file on disk.

        To save the time on re-computing weights, you can just keep the file,
        and set "reuse_weights=True" when initializing the regridder next time.
        """
        if os.path.exists(self.filename):
            print("Remove file {}".format(self.filename))
            os.remove(self.filename)
            print("You can still use the regridder because the weights "
                  "are already read into memory.")
        else:
            print("File {} is already removed.".format(self.filename))

    def __str__(self):
        info = ('xESMF Regridder \n'
                'Regridding algorithm:       {} \n'
                'Weight filename:            {} \n'
                'Reuse pre-computed weights? {} \n'
                'Input grid shape:           {} \n'
                'Output grid shape:          {} \n'
                'Periodic in longitude?      {}'
                .format(self.method,
                        self.filename,
                        self.reuse_weights,
                        (self.Ny_in, self.Nx_in),
                        (self.Ny_out, self.Nx_out),
                        self.periodic)
                )

        return info

    def __repr__(self):
        return self.__str__()

    def __call__(self, a):
        """
        Shortcut for ``regrid_numpy()`` and ``regrid_dataarray()``.

        Parameters
        ----------
        a : xarray DataArray or numpy array

        Returns
        -------
        xarray DataArray or numpy array
            Regridding results. Type depends on input.
        """
        # TODO: DataSet support

        if isinstance(a, np.ndarray):
            regrid_func = self.regrid_numpy
        elif isinstance(a, xr.DataArray):
            regrid_func = self.regrid_dataarray
        else:
            raise TypeError("input must be numpy array or xarray DataArray!")

        return regrid_func(a)

    def regrid_numpy(self, indata):
        """
        Regrid pure numpy array

        Parameters
        ----------
        indata : numpy array

        Returns
        -------
        outdata : numpy array

        """

        # check shape
        shape_horiz = indata.shape[-2:]  # the rightmost two dimensions
        assert shape_horiz == (self.Ny_in, self.Nx_in), (
             'The horizontal shape of input data is {}, different from that of'
             'the regridder {}!'.format(shape_horiz, (self.Ny_in, self.Nx_in))
             )

        outdata = apply_weights(self.A, indata, self.Ny_out, self.Nx_out)
        return outdata

    def regrid_dataarray(self, dr_in):
        """
        Regrid xarray DataArray, track metadata.

        Parameters
        ----------
        dr_in : xarray DataArray
            The rightmost two dimensions must be the same as ds_in.
            Can have arbitrary additional dimensions.

            Examples of valid dimensions:
            - (Nlat, Nlon), if ds_in has shape (Nlat, Nlon)
            - (N2, N1, Ny, Nx), if ds_in has shape (Ny, Nx)

        Returns
        -------
        dr_out : xarray DataArray
            On the same horizontal grid as ds_out, with extra dims in dr_in.

            Examples of returning dimensions,
            assuming ds_out has the shape of (Ny_out, Nx_out):
            - (Ny_out, Nx_out), if dr_in is 2D
            - (N2, N1, Ny_out, Nx_out), if dr_in has shape (N2, N1, Ny, Nx)

        """

        # apply regridding to pure numpy array
        outdata = self.regrid_numpy(dr_in.values)

        # track metadata
        dim_names = dr_in.dims
        horiz_dims = dim_names[-2:]
        extra_dims = dim_names[0:-2]

        varname = dr_in.name

        dr_out = xr.DataArray(outdata, dims=dim_names, name=varname)

        # append horizontal grid coordinate value
        dr_out.coords['lon'] = xr.DataArray(self._lon_out, dims=horiz_dims)
        dr_out.coords['lat'] = xr.DataArray(self._lat_out, dims=horiz_dims)

        # append extra dimension coordinate value
        for dim in extra_dims:
            dr_out.coords[dim] = dr_in.coords[dim]

        dr_out.attrs['regrid_method'] = self.method

        return dr_out

    def regrid_dataset(self, ds_in):
        raise NotImplementedError("Only support regrid_dataarray() for now.")
