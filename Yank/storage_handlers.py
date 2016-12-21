#!/usr/bin/env/python

# =============================================================================
# MODULE DOCSTRING
# =============================================================================

"""
Module which houses all the handling instructions for reading and writing to netCDF files for a given type.

This exists as its own module to keep the main storage module file smaller since any number of types may need to be
saved which special instructions for each.

====== WARNING ========
THIS IS VERY MUCH A WORK IN PROGRESS AND WILL PROBABLY MOSTLY BE SCRAPPED ON THE WAY

"""

# =============================================================================
# GLOBAL IMPORTS
# =============================================================================

import abc
import netCDF4 as nc
import numpy as np
import collections
import warnings

from simtk import unit

from .utils import typename, quantity_from_string

# TODO: Use the `with_metaclass` from yank.utils when we merge it in
ABC = abc.ABCMeta('ABC', (object,), {})  # compatible with Python 2 *and* 3


# =============================================================================
# MODULE VARIABLES
# =============================================================================

all_handlers = []
known_types = {handler.type_string: handler for handler in all_handlers}

# =============================================================================
# MODULE FUNCTIONS
# =============================================================================


def decompose_path(path):
    """

    Parameters
    ----------
    path : string
        Path to variable on the

    Returns
    -------
    structure : tuple
        Tuple of split apart path
    """
    return tuple((path_entry for path_entry in path.split('/') if path_entry != ''))

# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================


# =============================================================================
# ABSTRACT DRIVER HANDLER
# =============================================================================

class StorageIODriver(ABC):
    """
    Abstract class to define the basic functions any storage driver needs to read/write to the disk.
    The specific driver for a type of storage should be a subclass of this with its own
    encoders and decoders for specific file types.
    """
    def __init__(self, file_name, access_mode='w'):
        """

        Parameters
        ----------
        file_name : string
            Name of the file to read/write to of a given storage type
        access_mode : string, Default 'w', accepts 'w', 'r', 'a'
            Define how to access the file in either write, read, or append mode
        """
        # Internal map from Python Type <-> De/Encoder which handles the actual encoding and decoding of the data
        self._type_maps = {}
        self._variables = {}

    def add_deencoder(self, type_key, de_encoder):
        """
        Add new De/Encode to the specific driver class. This coder must know how to read/write and append to disk.

        Parameters
        ----------
        type_key : Unique immutable object
            Unique key that will be added to identify this de_encoder as part of the class
        de_encoder : Specific DeEnCoder class
            Class to handle all of the encoding of decoding of the variables

        """
        self._type_maps[type_key] = de_encoder

    @abc.abstractmethod
    def create_storage_variable(self, path, type_key):
        """
        Create a new variable on the disk and at the path location and store it as the given type.

        Parameters
        ----------
        path : string
            The way to identify the variable on the storage system. This can be either a variable name or a full path
            (such as in NetCDF files)
        type_key : Immutable object
            Type specifies the key identifier in the _type_maps added by the add_deencoder function. If type is not in
            _type_maps variable, an error is raised.

        Returns
        -------
        bound_de_encoder : DeEnCoder which is linked to a specific reference on the disk.

        """
        raise NotImplementedError("create_variable has not been implemented!")


# =============================================================================
# NetCDF IO Driver
# =============================================================================


class NetCDFIODriver(StorageIODriver):
    """
    Driver to handle all NetCDF IO operations, variable creation, and other operations.
    Can be extended to add new or modified type handlers
    """
    def get_netcdf_group(self, path):
        """
        Get the top level group on the NetCDF file, create the full path if not present

        Parameters
        ----------
        path : string
            Path to group on the disk

        Returns
        -------
        group : NetCDF Group
            Group object requested from file. All subsequent groups are created on the way down and can be accessed
            the same way.
        """
        try:
            group = self._groups[path]
        except KeyError:
            group = self._bind_group(path)
        finally:
            return group

    def get_variable_handler(self, path):
        """
        Get a variable IO object from disk at path. Raises an error if no storage object exists at that level

        Parameters
        ----------
        path : string
            Path to the variable/storage object on disk

        Returns
        -------
        handler : Subclass of NCVariableTypeHandler
            The handler tied to a specific variable and bound to it on the disk

        """
        try:
            # Check if the handler is already known to this instance
            handler = self._variables[path]
        except KeyError:
            try:
                # Attempt to read the disk and bind to that variable
                # Navigate the path down from top NC file to last entry
                head_group = self.ncfile
                split_path = decompose_path(path)
                for header in split_path[:-1]:
                    head_group = head_group.groups[header]
                # Check if this is a group type
                is_group = False
                if split_path[-1] in head_group.groups:
                    # Check if storage object IS a group (e.g. dict)
                    try:
                        obj = head_group.groups[split_path[-1]]
                        store_type = obj.getncattr('IODriver_Storage_Type')
                        if store_type == 'group':
                            variable = obj
                            is_group = True
                    except AttributeError:  # Trap the case of no group name in head_group, non-fatal
                        pass
                if not is_group:
                    # Bind to the specific variable instead since its not a group
                    variable = head_group.variables[split_path[-1]]
            except KeyError:
                raise KeyError("No variable found at {} on file!".format(path))
            try:
                # Bind to the storage type by mapping IODriver_Type -> Known TypeHandler
                data_type = variable.getncattr('IODriver_Type')
                head_path = '/' + '/'.join(split_path[:-1])
                target_name = split_path[-1]
                # Remember the group for the future while also getting storage binder
                group = self._bind_group(head_path)
                uninstanced_handler = self._IOMetaDataReaders[data_type]
                self._variables[path] = uninstanced_handler(self, target_name, storage_object=group)
                handler = self._variables[path]
            except AttributeError:
                raise AttributeError("Cannot auto-detect variable type, ensure that 'IODriver_Type' is a set ncattr")
            except KeyError:
                raise KeyError("No mapped type handler known for 'IODriver_Type' = '{}'".format(data_type))
        return handler

    def create_storage_variable(self, path, type_key):
        try:
            handler = self._type_maps[type_key]
        except KeyError:
            raise KeyError("No known Type Handler for given type!")
        split_path = decompose_path(path)
        # Bind groups as needed, splitting off the last entry
        # Ensure the head path has at least a '/' at the start
        head_path = '/' + '/'.join(split_path[:-1])
        target_name = split_path[-1]
        group = self._bind_group(head_path)
        self._variables[path] = handler(self, target_name, storage_object=group)
        return self._variables[path]

    @staticmethod
    def check_scalar_dimension(ncfile):
        """
        Check that the `scalar` dimension exists on file and create it if not

        """
        if 'scalar' not in ncfile.dimensions:
            ncfile.createDimension('scalar', 1)  # scalar dimension

    @staticmethod
    def check_infinite_dimension(ncfile, name='iteration'):
        """
        Check that the arbitrary infinite dimension exists on file and create it if not.

        Parameters
        ----------
        ncfile : NetCDF File
        name : string, optional, Default: 'iteration'
            Name of the dimension

        """
        if name not in ncfile.dimensions:
            ncfile.createDimension(name, 0)

    @staticmethod
    def check_iterable_dimension(ncfile, length=0):
        """
        Check that the dimension of appropriate size for a given iterable exists on file and create it if not

        Parameters
        ----------
        ncfile : NetCDF File
        length : int, Default: 0
            Length of the dimension, leave as 0 for infinite length

        """
        if type(length) is not int:
            raise TypeError("length must be an integer, not {}!".format(type(length)))
        if length < 0:
            raise ValueError("length must be >= 0")
        name = 'iterable{}'.format(length)
        if name not in ncfile.dimensions:
            ncfile.createDimension(name, length)

    def add_metadata(self, name, value, path='/'):
        """
        Add metadata to self on disk, extra bits of information that can be used for flags or other variables

        Parameters
        ----------
        name : string
            Name of the attribute you wish to assign
        value : any, but prefered string
            Extra meta data to add to the variable
        path : string, optional, Default: '/'
            Path to the object to assign metadata. If the object does not exist, an error is raised
            Not passing a path in attaches the data to the top level file
        """
        split_path = decompose_path(path)
        if len(split_path) == 0:
            self.ncfile.setncattr(name, value)
        elif split_path[0].strip() == '':  # Split this into its own elif since if the first is true this will fail
            self.ncfile.setncattr(name, value)
        elif path in self._groups:
            self._groups[path].setncattr(name, value)
        elif path in self._variables:
            self._variables[path].addmetadata(name, value)
        else:
            raise KeyError("Cannot assign metadata at path {} since no known object exists there! "
                           "Try get_netcdf_group or get_variable_handler first.".format(path))

    def _bind_group(self, path):
        """
        Bind a group to a particular path on the nc file. Note that this method creates the cascade of groups all the
        way to the final object if it can.

        Parameters
        ----------
        path : string
            Absolute path to the group as it appears on the NetCDF file.

        Returns
        -------
        group : NetCDF Group
            The group that path points to. Can be accessed by path through the ._groups dictionary after binding

        """
        # NetCDF4 creates the cascade of groups automatically or returns the group if already present
        # To simplify code, the cascade of groups is not stored in this class until called
        self._groups[path] = self.ncfile.createGroup(path)
        return self._groups[path]

    def __init__(self, file_name, access_mode='w'):
        super(NetCDFIODriver, self).__init__(file_name, access_mode=access_mode)
        # Bind to file
        self.ncfile = nc.Dataset(file_name, access_mode)
        self._groups = {}
        # Bind all of the Type Handlers
        self.add_deencoder(str, NCString)  # String
        self.add_deencoder(int, NCInt)  # Int
        self.add_deencoder(dict, NCDict)  # Dict
        self.add_deencoder(float, NCFloat)  # Float
        # List/tuple
        self.add_deencoder(list, NCIterable)
        self.add_deencoder(tuple, NCIterable)
        self.add_deencoder(np.ndarray, NCArray)  # Array
        self.add_deencoder(unit.Quantity, NCQuantity)  # Quantity
        # Bind the metadata reader types based on the dtype string of each class
        self._IOMetaDataReaders = {self._type_maps[key].type_string(): self._type_maps[key] for key in self._type_maps}


# =============================================================================
# ABSTRACT TYPE HANDLER
# =============================================================================


class NCVariableTypeHandler(ABC):
    """
    Pointer class which provides instructions on how to handle a given nc_variable
    """
    def __init__(self, parent_handler, target, storage_object=None):
        """
        Bind to a given nc_storage_object on ncfile with given final_target_name,
        If no nc_storage_object is None, it defaults to the top level ncfile
        Parameters
        ----------
        parent_handler : Parent NetCDF handler
            Class which can manipulate the NetCDF file at the top level for dimension creation and meta handling
        target : string
            String of the name of the object. Not explicitly a variable nor a group since the object could be either
        storage_object : NetCDF file or NetCDF group, optional, Default to ncfile on parent_handler
            Object the variable/object will be written onto

        """
        self._target = target
        # Eventual NetCDF object this class will be bound to
        self._bound_target = None
        # Target of the top level handler which houses all the variables
        self._parent_handler = parent_handler
        # Target object where the data read/written to this instance resides
        # Similar to the "directory" in a file system
        if storage_object is None:
            storage_object = self._parent_handler.ncfile
        self._storage_object = storage_object
        # Buffer to store metadata if assigned before binding
        self._metadata_buffer = {}
        # Flag for write/append mode
        self._output_mode = None

    @abc.abstractproperty  # TODO: Depreciate when we move to Python 3 fully with @abc.abstractmethod + @property
    def _dtype(self):
        """
        Define the Python data type for this variable

        Returns
        -------
        type

        """
        raise NotImplementedError("_dtype property has not been implemented in this subclass yet!")

    @property
    def dtype(self):
        """
        Create the property which calls the protected property

        Returns
        -------
        self._dtype : type

        """
        return self._dtype

    # @abc.abstractproperty
    @staticmethod
    def _dtype_type_string():
        """
        Short name of variable for strings and errors

        Returns
        -------
        string

        """
        # TODO: Replace with @abstractstaticmethod when on Python 3
        raise NotImplementedError("_dtype_type_string has not been implemented in this subclass yet!")

    @property
    def type_string(self):
        """
        Read the specified string name of the nc_variable type

        Returns
        -------
        type_string : string

        """
        return self._dtype_type_string()

    @abc.abstractmethod
    def _bind_read(self):
        """
        A one time event that binds this class to the object on disk. This method should set self._bound_target
        This function is unique to the read() function in that no data is attempted to write to the disk.
        Should raise error if the object is not found on disk (i.e. no data has been written to this location yet)
        Should raise error if the object on disk is incompatible with this type of TypeHandler.

        Returns
        -------
        None, but should set self._bound_target
        """
        raise NotImplementedError("_bind_read function has not been implemented in this subclass yet!")

    @abc.abstractmethod
    def _bind_write(self, data):
        """
        A one time event that binds this class to the object on disk. This method should set self._bound_target
        This function is unique to the write() function in that the data passed in should help create the storage object
        if not already on disk and prepare it for a write operation

        Parameters
        ----------
        data : Any type this TypeHandler can process
            Data which will be stored to disk of type. The data should not be written at this stage, but inspected to
            configure the storage as needed. In some cases, you may not even need the data.

        Returns
        -------
        None, but should set self._bound_target
        """
        raise NotImplementedError("_bind_write function has not been implemented in this subclass yet!")

    @abc.abstractmethod
    def _bind_append(self, data):
        """
        A one time event that binds this class to the object on disk. This method should set self._bound_target
        This function is unique to the append() function in that the data passed in should append what is at
        the location, or should create the object, then write the data with the first dimension infinite in size

        Parameters
        ----------
        data : Any type this TypeHandler can process
            Data which will be stored to disk of type. The data should not be written at this stage, but inspected to
            configure the storage as needed. In some cases, you may not even need the data.

        Returns
        -------
        None, but should set self._bound_target
        """
        raise NotImplementedError("_bind_append function has not been implemented in this subclass yet!")

    @abc.abstractmethod
    def read(self):
        """
        Return the property read from the ncfile

        Returns
        -------
        Given property read from the nc file and cast into the correct Python data type
        """

        raise NotImplementedError("Extracting stored NetCDF data into Python data has not been implemented!")

    @abc.abstractmethod
    def write(self, data):
        """
        Tell this writer how to write to the NetCDF file given the final object that it is bound to

        Parameters
        ----------
        data

        Returns
        -------

        """
        raise NotImplementedError("Writing Python data to NetCDF data has not been implemented!")

    @abc.abstractmethod
    def append(self, data):
        """
        Tell this writer how to write to the NetCDF file given the final object that it is bound to

        Parameters
        ----------
        data

        Returns
        -------

        """
        raise NotImplementedError("Writing Python data to NetCDF data has not been implemented!")

    def add_metadata(self, name, value):
        """
        Add metadata to self on disk, extra bits of information that can be used for flags or other variables
        This is NOT a staticmethod of the top dataset since you can buffer this before binding

        Parameters
        ----------
        name : string
            Name of the attribute you wish to assign
        value : any, but prefered string
            Extra meta data to add to the variable
        """
        if not self._bound_target:
            self._metadata_buffer[name] = value
        else:
            self._bound_target.setncattr(name,value)

    def _dump_metadata_buffer(self):
        """
        Dump the metadata buffer to file
        """
        if not self._bound_target:
            raise UnboundLocalError("Cannot dump the metadata buffer to target since no target exists!")
        self._bound_target.setncatts(self._metadata_buffer)
        self._metadata_buffer = {}

    @staticmethod
    def _convert_netcdf_store_type(stored_type):
        """
        Convert the stored NetCDF datatype from string to type without relying on unsafe eval() function

        Parameters
        ----------
        stored_type : string
            Read from ncfile.Variable.type

        Returns
        -------
        proper_type : type
            Python or module type

        """
        import importlib
        try:
            # Check if it's a builtin type
            try:  # Python 2
                module = importlib.import_module('__builtin__')
            except:  # Python 3
                module = importlib.import_module('builtins')
            proper_type = getattr(module, stored_type)
        except AttributeError:
            # if not, separate module and class
            module, stored_type = stored_type.rsplit(".", 1)
            module = importlib.import_module(module)
            proper_type = getattr(module, stored_type)
        return proper_type

    def _check_write_append(self):
        """
        Set the write and append flags, should only be called from within _bind_write and _bind_append after being bound
        or within read if not bound.
        """
        if self._bound_target.getncattr('IODriver_Appendable'):
            self._output_mode = 'a'
        else:
            self._output_mode = 'r'

    def _attempt_storage_read(self):
        """
        This is a helper function to try and read the target from the disk then do some validation checks common to
        every _bind_read call. Helps cut down on recoding.

        Returns
        -------
        None, but should try to set _bound_target from disk
        """
        self._bound_target = self._storage_object[self._target]
        # Ensure that the target we bind to matches the type of driver
        try:
            if self._bound_target.getncattr('IODriver_Type') != self.type_string:
                raise TypeError("Storage target on NetCDF file is of type {} but this driver is designed to handle "
                                "type {}!".foramt(self._bound_target.getncattr('IODriver_Type'), self.type_string))
        except AttributeError:
            warnings.warn("This TypeHandler cannot detect storage type for {}. .write() and .append() operations "
                          "will not work and .read() operations may work", RuntimeWarning)

# =============================================================================
# NETCDF NON-COMPOUND TYPE CODERS
# =============================================================================

# Decoders: Convert from NC variable to python type
# Encoders: Decompose Python Type into something NC storable data

def NCStringDecoder(nc_variable):
    if nc_variable.shape == ():
        return str(nc_variable.getValue())
    else:
        return nc_variable[:, 0].astype(str)


def NCStringEncoder(data):
    packed_data = np.empty(1, 'O')
    packed_data[0] = data
    return packed_data


def NCIntDecoder(nc_variable):
    return nc_variable[:].astype(int)


def NCIntEncoder(data):
    return data


def NCFloatDecoder(nc_variable):
    return nc_variable[:].astype(float)


def NCFloatEncoder(data):
    return data


# There really isn't anything that needs to happen here, arrays are the ideal type
# Leaving these as explicit coders in case we need to change them later
def NCNPArrayDecoder(nc_variable):
    return nc_variable[:]


def NCNPArrayEncoder(data):
    return data


# List and tuple iterables, assumes contents are the same type.
# Use dictionaries for compound types
def NCIterableDecoder(nc_variable):
    shape = nc_variable.shape
    type_name = nc_variable.getncattr('type')
    output_type = NCVariableTypeHandler._convert_netcdf_store_type(type_name)
    if len(shape) == 1:  # Determine if iterable
        output = output_type(nc_variable[:])
    else:  # Handle long form iterable by making an array of iterable type
        output = np.empty(shape[0], dtype = output_type)
        for i in range(shape[0]):
            output[i] = output_type(nc_variable[i])
    return output


def NCIterableEncoder(data):
    nelements = len(data)
    element_type = type(data[0])
    element_type_name = typename(element_type)
    return nelements, element_type, element_type_name


# =============================================================================
# REAL TYPE HANDLERS
# =============================================================================

# Array

class NCArray(NCVariableTypeHandler):
    """
    NetCDF handler for numpy arrays
    """
    @property
    def _dtype(self):
        return np.ndarray

    @staticmethod
    def _dtype_type_string():
        return "numpy.ndarray"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
            # Handle variable size objects
            # This line will not happen unless target is real, so None should not be returned by _check_write_append
            self._check_write_append()
            if self._output_mode is 'a':
                self._save_shape = self._bound_target.shape[1:]
            else:
                self._save_shape = self._bound_target.shape
        except KeyError as e:
            raise e

    def _bind_write(self, data):
        try:
            self._bind_read()
        except KeyError:
            data_shape, data_base_type, data_type_name = self._determine_data_information(data)
            dims = []
            for length in data_shape:
                NetCDFIODriver.check_iterable_dimension(self._parent_handler.ncfile, length=length)
                dims.append('iterable{}'.format(length))
            self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                     dimensions=dims,
                                                                     chunksize=data_shape)
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', str(data_base_type))
            self._unit = 'NoneType'
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 0)
        self._dump_metadata_buffer()
        self._check_write_append()

    def _bind_append(self, data):
        try:
            self._bind_read()
        except KeyError:
            data_shape, data_base_type, data_type_name = self._determine_data_information(data)
            dims = ['iteration']
            NetCDFIODriver.check_infinite_dimension(self._parent_handler.ncfile)
            for length in data_shape:
                NetCDFIODriver.check_iterable_dimension(self._parent_handler.ncfile, length=length)
                dims.append('iterable{}'.format(length))
            self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                     dimensions=dims,
                                                                     chunksize=(1,) + data_shape)
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', str(data_base_type))
            self._unit = 'NoneType'
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 1)
        self._dump_metadata_buffer()
        self._check_write_append()

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        if not self._output_mode:
            self._check_write_append()
        return NCNPArrayDecoder(self._bound_target)

    def write(self, data):
        # Check type
        if not isinstance(type(data), self._dtype):
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'w':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        if self._save_shape != self._compare_shape(data):
            raise ValueError("Input data must be of shape {} but is instead of shape {}!".format(
                self._compare_shape(data), self._save_shape)
            )
        # Save data
        packaged_data = NCNPArrayEncoder(data)
        self._bound_target[:] = packaged_data
        return

    def append(self, data):
        # Check type
        if not isinstance(type(data), self._dtype):
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'a':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        if self._save_shape != self._compare_shape(data):
            raise ValueError("Input data must be of shape {} but is instead of shape {}!".format(
                self._compare_shape(data), self._save_shape)
            )
        # Save data
        packaged_data = NCNPArrayEncoder(data)
        # Determine current current length and therefore the last index
        length = self._bound_target.shape[0]
        self._bound_target[length, :] = packaged_data


    @staticmethod
    def _determine_data_information(data):
        # Make common _bind functions a single function
        data_shape = data.shape
        data_base_type = data.dtype
        data_type_name = typename(type(data))
        return data_shape, data_base_type, data_type_name


class NCFloat(NCVariableTypeHandler):
    """
    NetCDF handler for floats
    """
    @property
    def _dtype(self):
        return float

    @staticmethod
    def _dtype_type_string():
        return "float"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
            # Handle variable size objects
            # This line will not happen unless target is real, so None should not be returned by _check_write_append
            self._check_write_append()
            if self._output_mode is 'a':
                self._save_shape = self._bound_target.shape[1:]
            else:
                self._save_shape = self._bound_target.shape
        except KeyError as e:
            raise e

    def _bind_write(self, data):
        try:
            self._bind_read()
        except KeyError:
            NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
            self._bound_target = self._storage_object.createVariable(self._target, float,
                                                                     dimensions='scalar',
                                                                     chunksize=(1,))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', 'float')
            self._unit = 'NoneType'
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 0)
        self._dump_metadata_buffer()
        self._check_write_append()

    def _bind_append(self, data):
        try:
            self._bind_read()
        except KeyError:
            NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
            NetCDFIODriver.check_infinite_dimension(self._parent_handler.ncfile)
            self._bound_target = self._storage_object.createVariable(self._target, float,
                                                                     dimensions=['iteration', 'scalar'],
                                                                     chunksize=(1, 1))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', 'float')
            self._unit = 'NoneType'
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 1)
        self._dump_metadata_buffer()
        self._check_write_append()
        return

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        if not self._output_mode:
            self._check_write_append()
        return NCFloatDecoder(self._bound_target)

    def write(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'w':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        # Save data
        self._bound_target[:] = NCFloatEncoder(data)
        return

    def append(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_append(data)
        # Check writeable
        if self._output_mode != 'a':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        # Determine current current length and therefore the last index
        length = self._bound_target.shape[0]
        # Save data
        self._bound_target[length, :] = NCFloatEncoder(data)


class NCInt(NCVariableTypeHandler):
    """
    NetCDF handler for integers.
    """
    @property
    def _dtype(self):
        return int

    @staticmethod
    def _dtype_type_string():
        return "int"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
            # Handle variable size objects
            # This line will not happen unless target is real, so None should not be returned by _check_write_append
            self._check_write_append()
            if self._output_mode is 'a':
                self._save_shape = self._bound_target.shape[1:]
            else:
                self._save_shape = self._bound_target.shape
        except KeyError as e:
            raise e

    def _bind_write(self, data):
        try:
            self._bind_read()
        except KeyError:
            NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
            self._bound_target = self._storage_object.createVariable(self._target, int,
                                                                     dimensions='scalar',
                                                                     chunksize=(1,))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', 'int')
            self._unit = 'NoneType'
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 0)
        self._dump_metadata_buffer()
        self._check_write_append()

    def _bind_append(self, data):
        try:
            self._bind_read()
        except KeyError:
            NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
            NetCDFIODriver.check_infinite_dimension(self._parent_handler.ncfile)
            self._bound_target = self._storage_object.createVariable(self._target, int,
                                                                     dimensions=['iteration', 'scalar'],
                                                                     chunksize=(1, 1))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', 'int')
            self._unit = 'NoneType'
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 1)
        self._dump_metadata_buffer()
        self._check_write_append()
        return

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        if not self._output_mode:
            self._check_write_append()
        return NCIntDecoder(self._bound_target)

    def write(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'w':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        # Save data
        self._bound_target[:] = NCIntEncoder(data)
        return

    def append(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_append(data)
        # Check writeable
        if self._output_mode != 'a':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        # Determine current current length and therefore the last index
        length = self._bound_target.shape[0]
        # Save data
        self._bound_target[length, :] = NCIntEncoder(data)


class NCIterable(NCVariableTypeHandler):
    """
    NetCDF handler for lists and tuples
    """
    @property
    def _dtype(self):
        return collections.Iterable

    @staticmethod
    def _dtype_type_string():
        return "iterable"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
            # Handle variable size objects
            # This line will not happen unless target is real, so None should not be returned by _check_write_append
            self._check_write_append()
            if self._output_mode is 'a':
                self._save_shape = self._bound_target.shape[1:]
            else:
                self._save_shape = self._bound_target.shape
        except KeyError as e:
            raise e

    def _bind_write(self, data):
        try:
            self._bind_read()
        except KeyError:
            data_shape, data_base_type, data_type_name = self._determine_data_information(data)
            NetCDFIODriver.check_iterable_dimension(self._parent_handler.ncfile, length=data_shape)
            self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                     dimensions='iterable{}'.format(data_shape),
                                                                     chunksize=(data_shape,))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', data_type_name)
            self._unit = "NoneType"
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 0)
            self._save_shape = data_shape
        self._dump_metadata_buffer()
        self._check_write_append()
        return

    def _bind_append(self, data):
        try:
            self._bind_read()
        except KeyError:
            data_shape, data_base_type, data_type_name = self._determine_data_information(data)
            NetCDFIODriver.check_infinite_dimension(self._parent_handler.ncfile)
            NetCDFIODriver.check_iterable_dimension(self._parent_handler.ncfile, length=data_shape)
            dims = ['iteration', 'iterable{}'.format(data_shape)]
            self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                     dimensions=dims,
                                                                     chunksize=(1, data_shape))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', data_type_name)
            self._unit = "NoneType"
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 1)
            self._save_shape = data_shape
        self._dump_metadata_buffer()
        self._check_write_append()
        return

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        if not self._output_mode:
            self._check_write_append()
        return NCIterableDecoder(self._bound_target)

    def write(self, data):
        # Check type
        if not isinstance(type(data), self._dtype):
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'w':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        if self._save_shape != self._compare_shape(data):
            raise ValueError("Input data must be of shape {} but is instead of shape {}!".format(
                self._compare_shape(data), self._save_shape)
            )
        # Save data
        packaged_data = NCIterableEncoder(data)
        self._bound_target[:] = packaged_data
        return

    def append(self, data):
        # Check type
        if not isinstance(type(data), self._dtype):
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'a':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        if self._save_shape != self._compare_shape(data):
            raise ValueError("Input data must be of shape {} but is instead of shape {}!".format(
                self._compare_shape(data), self._save_shape)
            )
        # Save data
        packaged_data = NCIterableEncoder(data)
        # Determine current current length and therefore the last index
        length = self._bound_target.shape[0]
        self._bound_target[length, :] = packaged_data

    @staticmethod
    def _determine_data_information(data):
        # Make common _bind functions a single function
        data_type_name = typename(type(data))
        data_base_type = data[0]
        data_shape = len(data)
        return data_shape, data_base_type, data_type_name


class NCQuantity(NCVariableTypeHandler):
    """
    NetCDF handler for ALL simtk.unit.Quantity's
    """
    @property
    def _dtype(self):
        return unit.Quantity

    @staticmethod
    def _dtype_type_string():
        return "quantity"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
            # Handle variable size objects
            # This line will not happen unless target is real, so None should not be returned by _check_write_append
            self._check_write_append()
            if self._output_mode is 'a':
                self._save_shape = self._bound_target.shape[1:]
            else:
                self._save_shape = self._bound_target.shape
            self._unit = self._bound_target.getncattr('IODriver_Unit')
            self._set_codifiers(self._bound_target.getncattr('type'))
        except KeyError as e:
            raise e

    def _bind_write(self, data):
        try:
            self._bind_read()
        except KeyError:
            data_shape, data_base_type, data_type_name = self._determine_data_information(data)
            if data_shape == 1:  # Single dimension quantity
                NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
                self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                         dimensions='scalar',
                                                                         chunksize=(1,))
            else:
                dims = []
                for length in data_shape:
                    NetCDFIODriver.check_iterable_dimension(self._parent_handler.ncfile, length=length)
                    dims.append('iterable{}'.format(length))
                self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                         dimensions=dims,
                                                                         chunksize=data_shape)

            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', data_type_name)
            self._unit = str(data.unit)
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 0)
            self._save_shape = data_shape
        self._dump_metadata_buffer()
        self._check_write_append()
        self._set_codifiers(data_type_name)
        return

    def _bind_append(self, data):
        try:
            self._bind_read()
        except KeyError:
            data_shape, data_base_type, data_type_name = self._determine_data_information(data)
            NetCDFIODriver.check_infinite_dimension(self._parent_handler.ncfile)
            if data_shape == 1:  # Single dimension quantity
                NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
                self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                         dimensions=['iteration', 'scalar'],
                                                                         chunksize=(1, 1))
            else:
                dims = ['iteration']
                for length in data_shape:
                    NetCDFIODriver.check_iterable_dimension(self._parent_handler.ncfile, length=length)
                    dims.append('iterable{}'.format(length))
                self._bound_target = self._storage_object.createVariable(self._target, data_base_type,
                                                                         dimensions=dims,
                                                                         chunksize=(1,) + data_shape)
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            self.add_metadata('type', data_type_name)
            self._unit = str(data.unit)
            self.add_metadata('IODriver_Unit', self._unit)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 1)
            self._save_shape = data_shape
        self._dump_metadata_buffer()
        self._check_write_append()
        self._set_codifiers(data_type_name)
        return

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        if not self._output_mode:
            self._check_write_append()
        data = self._decoder(self._bound_target)
        unit_name = self._bound_target.getncattr('IODriver_Unit')
        # Do some things to handle the way quantity_from_string parses units that only have a denominator (e.g. Hz)
        if unit_name[0] == '/':
            unit_name = "(" + unit_name + ")**-1"
        cast_unit = quantity_from_string(unit_name)
        if isinstance(cast_unit, unit.Quantity):
            cast_unit = cast_unit.unit
        return data * cast_unit

    def write(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write(data)
        # Check writeable
        if self._output_mode != 'w':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        if self._save_shape != self._compare_shape(data):
            raise ValueError("Input data must be of shape {} but is instead of shape {}!".format(
                self._compare_shape(data), self._save_shape)
            )
        if self._unit != str(data.unit):
            raise ValueError("Input data must have units of {}, but instead is {}".format(self._unit,
                                                                                          str(data.unit)))
        # Save data
        # Strip Unit
        data_unit = data.unit
        data_value = data / data_unit
        packaged_data = self._encoder(data_value)
        self._bound_target[:] = packaged_data
        return

    def append(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_append(data)
        # Check writeable
        if self._output_mode != 'a':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        if self._save_shape != self._compare_shape(data):
            raise ValueError("Input data must be of shape {} but is instead of shape {}!".format(
                self._compare_shape(data), self._save_shape)
            )
        if self._unit != str(data.unit):
            raise ValueError("Input data must have units of {}, but instead is {}".format(self._unit,
                                                                                          str(data.unit)))
        # Determine current current length and therefore the last index
        length = self._bound_target.shape[0]
        # Save data
        # Strip Unit
        data_unit = data.unit
        data_value = data / data_unit
        packaged_data = self._encoder(data_value)
        self._bound_target[length, :] = packaged_data

    def _determine_data_information(self, data):
        # Make common _bind functions a single function
        data_unit = data.unit
        data_value = data / data_unit
        data_type_name = typename(type(data_value))
        try:
            data_shape = data_value.shape
            data_base_type = type(data_value.flatten()[0])
            self._compare_shape = lambda x: x.shape
        except AttributeError:  # Trap not array
            try:
                data_shape = tuple(len(data_value))
                data_base_type = type(data_value[0])
                self._compare_shape = lambda x: tuple(len(x))
            except TypeError:  # Trap not iterable
                data_shape = 1
                data_base_type = type(data_value)
                self._compare_shape = lambda x: 1
        return data_shape, data_base_type, data_type_name

    def _set_codifiers(self, stype):
        # Assign the coders in a single block
        if stype == 'int':
            self._encoder = NCIntEncoder
            self._decoder = NCIntDecoder
        elif stype == 'float':
            self._encoder = NCFloatEncoder
            self._decoder = NCFloatDecoder
        elif stype == 'list' or stype == 'tuple':
            self._encoder = NCIterableEncoder
            self._decoder = NCIterableDecoder
        elif 'ndarray' in stype:
            self._encoder = NCNPArrayEncoder
            self._decoder = NCNPArrayDecoder
        else:
            raise TypeError("NCQuantity does not know how to handle a quantity of type {}!".format(stype))


class NCString(NCVariableTypeHandler):
    """
    NetCDF handling strings
    I don't expect the unit to be affixed to a string, so there is no processing for it
    """
    @property
    def _dtype(self):
        return str

    @staticmethod
    def _dtype_type_string():
        return "str"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
        except KeyError as e:
            raise e

    def _bind_write(self):
        try:
            self._bind_read()
        except KeyError:
            NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
            self._bound_target = self._storage_object.createVariable(self._target, str, dimensions='scalar', chunksize=(1,))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 0)
        self._dump_metadata_buffer()
        self._check_write_append()
        return

    def _bind_append(self, infinite_dimension='iteration'):
        try:
            self._bind_read()
        except KeyError:
            NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
            NetCDFIODriver.check_infinite_dimension(self._parent_handler.ncfile)
            self._bound_target = self._storage_object.createVariable(
                self._target,
                str,
                dimensions=(infinite_dimension, 'scalar'),
                chunksize=(1, 1))
            # Specify a way for the IO Driver stores data
            self.add_metadata('IODriver_Type', self.type_string)
            # Specify the type of storage object this should tie to
            self.add_metadata('IODriver_Storage_Type', 'variable')
            self.add_metadata('IODriver_Appendable', 1)
        self._dump_metadata_buffer()
        self._check_write_append()
        return

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        if not self._output_mode:
            self._check_write_append()
        return NCStringDecoder(self._bound_target)

    def write(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_write()
        # Check writeable
        if self._output_mode != 'w':
            raise TypeError("{} at {} was saved as appendable data! Cannot overwrite, must use append()".format(
                self.type_string, self._target)
            )
        # Save data
        storeable_data = NCStringEncoder(data)
        self._bound_target[:] = storeable_data
        return

    def append(self, data):
        # Check type
        if type(data) is not self._dtype:
            raise TypeError("Invalid data type on variable {}.".format(self._target))
        # Bind
        if self._bound_target is None:
            self._bind_append()
        # Can append
        if self._output_mode != 'a':
            raise TypeError("{} at {} was saved as static, non-appendable data! Cannot append, must use write()".format(
                self.type_string, self._target)
            )
        # Determine current current length and therefore the last index
        length = self._bound_target.shape[0]
        # Save data
        storable_data = NCStringEncoder(data)
        self._bound_target[length, :] = storable_data
        return


class NCDict(NCVariableTypeHandler):
    """
    NetCDF handling of dictionaries
    This is by in-large the most complicated object to store since its the combination of all types
    """
    @property
    def _dtype(self):
        return dict

    @staticmethod
    def _dtype_type_string():
        return "dict"

    def _bind_read(self):
        try:
            self._attempt_storage_read()
        except KeyError as e:
            raise e

    def _bind_write(self):
        # Because the _bound_target in this case is a NetCDF group, no initial data is writen.
        # The write() function handles that though
        try:
            self._bind_read()
        except KeyError:
            self._bound_target = self._storage_object.createGroup(self._target)
        # Specify a way for the IO Driver stores data
        self.add_metadata('IODriver_Type', self.type_string)
        # Specify the type of storage object this should tie to
        self.add_metadata('IODriver_Storage_Type', 'group')
        self.add_metadata('IODriver_Appendable', 0)
        self._dump_metadata_buffer()

    def _bind_append(self):
        # TODO: Determine how to do this eventually
        raise NotImplementedError("Dictionaries cannot be appended to!")

    def read(self):
        if self._bound_target is None:
            self._bind_read()
        return self._decode_dict()

    def write(self, data):
        if self._bound_target is None:
            self._bind_write()
        self._encode_dict(data)

    def append(self, data):
        self._bind_append()

    def _decode_dict(self):
        """

        Returns
        -------
        output_dict : dict
            The restored dictionary as a dict.

        """
        output_dict = dict()
        for output_name in self._bound_target.variables.keys():
            # Get NetCDF variable.
            output_ncvar = self._bound_target.variables[output_name]
            type_name = output_ncvar.getncattr('type')
            # TODO: Remove the if/elseif structure into one handy function
            # Get output value.
            if type_name == 'NoneType':
                output_value = None
            else:  # Handle all Types not None
                output_type = NCVariableTypeHandler._convert_netcdf_store_type(type_name)
                if output_ncvar.shape == ():
                    # Handle Standard Types
                    output_value = output_type(output_ncvar.getValue())
                elif output_ncvar.shape[0] >= 0:
                    # Handle array types
                    output_value = np.array(output_ncvar[:], output_type)
                    # TODO: Deal with values that are actually scalar constants.
                    # TODO: Cast to appropriate type
                else:
                    # Handle iterable types?
                    # TODO: Figure out what is actually cast here
                    output_value = output_type(output_ncvar[0])
            # If Quantity, assign unit.
            if 'units' in output_ncvar.ncattrs():
                output_unit_name = output_ncvar.getncattr('units')
                if output_unit_name[0] == '/':
                    output_value = str(output_value) + output_unit_name
                else:
                    output_value = str(output_value) + '*' + output_unit_name
                output_value = quantity_from_string(output_value)
            # Store output.
            output_dict[output_name] = output_value

        return output_dict

    def _encode_dict(self, data):
        """
        Store the contents of a dict in a NetCDF file.

        Parameters
        ----------
        data : dict
            The dict to store.

        """
        NetCDFIODriver.check_scalar_dimension(self._parent_handler.ncfile)
        for datum_name in data.keys():
            # Get entry value.
            datum_value = data[datum_name]
            # If Quantity, strip off units first.
            datum_unit = None
            if type(datum_value) == unit.Quantity:
                datum_unit = datum_value.unit
                datum_value = datum_value / datum_unit
            # Store the Python type.
            datum_type = type(datum_value)
            datum_type_name = typename(datum_type)
            # Handle booleans
            if type(datum_value) == bool:
                datum_value = int(datum_value)
            # Store the variable.
            if type(datum_value) == str:
                ncvar = self._bound_target.createVariable(datum_name, type(datum_value), 'scalar')
                storable_data = NCStringEncoder(datum_value)
                ncvar[:] = storable_data
                ncvar.setncattr('type', datum_type_name)
            elif isinstance(datum_value, collections.Iterable):
                nelements, element_type, element_type_name = NCIterableEncoder(datum_value)
                self._bound_target.createDimension(datum_name, nelements) # unlimited number of iterations
                ncvar = self._bound_target.createVariable(datum_name, element_type, (datum_name,))
                for (i, element) in enumerate(datum_value):
                    ncvar[i] = element
                ncvar.setncattr('type', element_type_name)
            elif datum_value is None:
                ncvar = self._bound_target.createVariable(datum_name, int)
                ncvar.assignValue(0)
                ncvar.setncattr('type', datum_type_name)
            else:
                ncvar = self._bound_target.createVariable(datum_name, type(datum_value))
                ncvar.assignValue(datum_value)
                ncvar.setncattr('type', datum_type_name)
            if datum_unit:
                ncvar.setncattr('units', str(datum_unit))
        return


# =============================================================================
# LOGIC TIME!
# =============================================================================

"""
Storage Interface (SI) passes down a name of file and instantiates a Storage Handler (SH) instance
SI Directory/Variable (SIDV) objects send and request data down
SH Binds to a file (if present) and waits for next instructions, all logic follows instructions from SI => SH

From the SIDV:
SIDV.{write/append/fetch}
    If not VARIABLE:
        SH.bind_variable(PATH, MODE?)
    VARIABLE.{write/fetch/append}


SI.write_metadata(NAME, DATA):
    Add NAME as a metadata entry with DATA attached:
        *def write_metadata(NAME, DATA)*
SIDV.{write/append/fetch} initiate a BIND event, cascading up
    PATH will be generated from the SIDV and passed down
    Convert PATH to groups and variable
    Bind .{write/append}
        Check if PATH in FILE
            Yes?
                Fetch STORAGEOBJECT (SO), TYPE
                Bind (D)E(N)CODER (denC) based on TYPE
                Bind UNIT to denC
                Bind SO to denC
                Return denC to SIDV
            No?



SIDV.{fetch}
    PATH passed down by the SIDV
    If PATH not in BOUNDSET:
        If not on_file(PATH):
            Raise Error
        else:
            Fetch STORAGEOBJECT, TYPE, UNIT from FILE at PATH
            BOUNDSET[PATH] = denC(STORAGEOBJECT, TYPE, UNIT)
    denC = BOUNDSET[PATH]
    denC.



BOUNDSET is a dict of objects which point to different denC's with keywords as fed PATHs from SIDV
Needed Functions of SH:
    write_metadata(NAME, DATA)
        Returns None
    on_file(PATH)
        Returns Bool
    read_file(PATH)     Might combine with on_file
        Returns STORAGEOBJECT, TYPE, UNIT
Needed Functions of denC:
    unit
        Property, returns units of the bound STORAGEOBJECT









Write Bound Present
Write Unbound Present
Append Bound Present
append Unbound Present
Fetch Bound Present
Fetch Unbound Present
Write Bound NotPresent
Write Unbound NotPresent
Append Bound NotPresent
append Unbound NotPresent

{Fetch Bound NotPresent
Fetch Unbound NotPresent}
    Raise Error
"""