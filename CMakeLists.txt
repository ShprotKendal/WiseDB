cmake_minimum_required(VERSION 3.18)
project(wisedb VERSION 3.0.0 LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(WISEDB_SOURCES
    src/c/json.c
    src/c/wisedb.c
    src/c/store.c
    src/c/format.c
    src/c/index.c
    src/c/settings.c
    src/c/retrieve.c
    src/c/catalog_filter.c
)

add_library(wisedb_core STATIC ${WISEDB_SOURCES})
add_library(wisedb SHARED ${WISEDB_SOURCES})
add_library(wisedb_static STATIC ${WISEDB_SOURCES})
set_target_properties(wisedb_static PROPERTIES OUTPUT_NAME wisedb)
target_include_directories(wisedb_core PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src/c>
    $<INSTALL_INTERFACE:include>
)
target_include_directories(wisedb PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src/c>
    $<INSTALL_INTERFACE:include>
)
target_include_directories(wisedb_static PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src/c>
    $<INSTALL_INTERFACE:include>
)
if(NOT WIN32)
    target_link_libraries(wisedb_core PUBLIC m)
    target_link_libraries(wisedb PUBLIC m)
    target_link_libraries(wisedb_static PUBLIC m)
endif()

add_executable(wisedb_test_c tests/c/test_core.c)
target_link_libraries(wisedb_test_c PRIVATE wisedb_core)

add_executable(wisedb_test_semantics tests/c/test_semantics.c)
target_link_libraries(wisedb_test_semantics PRIVATE wisedb_core)

if(UNIX)
    add_executable(wisedb_test_fault_injection
        ${WISEDB_SOURCES}
        src/c/fault_injection.c
        tests/c/test_fault_injection.c
    )
    target_include_directories(wisedb_test_fault_injection PRIVATE include src/c)
    target_compile_definitions(wisedb_test_fault_injection PRIVATE WISEDB_ENABLE_FAULT_INJECTION)
    if(NOT WIN32)
        target_link_libraries(wisedb_test_fault_injection PRIVATE m)
    endif()

    add_executable(wisedb_test_recovery
        src/c/codec.c
        src/c/wal.c
        src/c/batch.c
        src/c/sstable.c
        src/c/manifest.c
        src/c/storage.c
        src/c/fault_injection.c
        tests/c/test_recovery.c
    )
    target_include_directories(wisedb_test_recovery PRIVATE src/c)
    target_compile_definitions(wisedb_test_recovery PRIVATE WISEDB_ENABLE_FAULT_INJECTION)
    set_target_properties(wisedb_test_recovery PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
endif()

add_executable(wisedb_cpp_hello examples/cpp/hello.cpp)
target_link_libraries(wisedb_cpp_hello PRIVATE wisedb_static)

enable_testing()
add_test(NAME core COMMAND wisedb_test_c)
add_test(NAME semantics COMMAND wisedb_test_semantics)
if(UNIX)
    add_test(NAME fault_injection COMMAND wisedb_test_fault_injection)
    add_test(NAME recovery COMMAND wisedb_test_recovery)
endif()

if(UNIX)
    add_executable(wisedb_test_wal src/c/codec.c src/c/wal.c tests/c/test_wal.c)
    target_include_directories(wisedb_test_wal PRIVATE src/c)
    set_target_properties(wisedb_test_wal PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    add_test(NAME wal COMMAND wisedb_test_wal)

    add_executable(wisedb_test_batch src/c/codec.c src/c/wal.c src/c/batch.c tests/c/test_batch.c)
    target_include_directories(wisedb_test_batch PRIVATE src/c)
    set_target_properties(wisedb_test_batch PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    add_test(NAME batch COMMAND wisedb_test_batch)

    add_executable(wisedb_test_sstable src/c/codec.c src/c/sstable.c tests/c/test_sstable.c)
    target_include_directories(wisedb_test_sstable PRIVATE src/c)
    set_target_properties(wisedb_test_sstable PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    add_test(NAME sstable COMMAND wisedb_test_sstable)

    add_executable(wisedb_test_manifest src/c/codec.c src/c/sstable.c src/c/manifest.c tests/c/test_manifest.c)
    target_include_directories(wisedb_test_manifest PRIVATE src/c)
    set_target_properties(wisedb_test_manifest PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON)
    add_test(NAME manifest COMMAND wisedb_test_manifest)
endif()

include(GNUInstallDirs)
install(TARGETS wisedb_core wisedb wisedb_static
    EXPORT wisedbTargets
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
install(FILES include/wisedb.h include/wisedb.hpp
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)
install(EXPORT wisedbTargets
    FILE wisedbTargets.cmake
    NAMESPACE wisedb::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/wisedb
)

include(CMakePackageConfigHelpers)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/wisedbConfigVersion.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion
)
configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/wisedbConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/wisedbConfig.cmake"
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/wisedb
)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/wisedbConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/wisedbConfigVersion.cmake"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/wisedb
)

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/wisedb.pc.in"
    "${CMAKE_CURRENT_BINARY_DIR}/wisedb.pc"
    @ONLY
)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/wisedb.pc"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig
)
