#----------------------------------------------------------------
# Generated CMake target import file for configuration "Debug".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "wisedb::wisedb_core" for configuration "Debug"
set_property(TARGET wisedb::wisedb_core APPEND PROPERTY IMPORTED_CONFIGURATIONS DEBUG)
set_target_properties(wisedb::wisedb_core PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_DEBUG "C"
  IMPORTED_LOCATION_DEBUG "${_IMPORT_PREFIX}/lib/libwisedb_core.a"
  )

list(APPEND _cmake_import_check_targets wisedb::wisedb_core )
list(APPEND _cmake_import_check_files_for_wisedb::wisedb_core "${_IMPORT_PREFIX}/lib/libwisedb_core.a" )

# Import target "wisedb::wisedb" for configuration "Debug"
set_property(TARGET wisedb::wisedb APPEND PROPERTY IMPORTED_CONFIGURATIONS DEBUG)
set_target_properties(wisedb::wisedb PROPERTIES
  IMPORTED_LOCATION_DEBUG "${_IMPORT_PREFIX}/lib/libwisedb.dylib"
  IMPORTED_SONAME_DEBUG "@rpath/libwisedb.dylib"
  )

list(APPEND _cmake_import_check_targets wisedb::wisedb )
list(APPEND _cmake_import_check_files_for_wisedb::wisedb "${_IMPORT_PREFIX}/lib/libwisedb.dylib" )

# Import target "wisedb::wisedb_static" for configuration "Debug"
set_property(TARGET wisedb::wisedb_static APPEND PROPERTY IMPORTED_CONFIGURATIONS DEBUG)
set_target_properties(wisedb::wisedb_static PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_DEBUG "C"
  IMPORTED_LOCATION_DEBUG "${_IMPORT_PREFIX}/lib/libwisedb.a"
  )

list(APPEND _cmake_import_check_targets wisedb::wisedb_static )
list(APPEND _cmake_import_check_files_for_wisedb::wisedb_static "${_IMPORT_PREFIX}/lib/libwisedb.a" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
