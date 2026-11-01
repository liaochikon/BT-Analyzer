cmake_minimum_required(VERSION 3.25)

project(bt_stem LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT MSVC)
  message(FATAL_ERROR "This backbone is configured for the Windows MSVC build.")
endif()

include(FetchContent)

# soxr 0.1.3 still declares a very old CMake minimum.
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)

set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(WITH_OPENMP OFF CACHE BOOL "" FORCE)
set(WITH_LSR_BINDINGS OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
  soxr
  URL https://github.com/chirlu/soxr/archive/refs/tags/0.1.3.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  PATCH_COMMAND ${CMAKE_COMMAND} -DSOXR_DIR=<SOURCE_DIR> -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/patch_soxr.cmake)
FetchContent_Declare(
  Catch2
  URL https://github.com/catchorg/Catch2/archive/refs/tags/v3.8.1.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(soxr Catch2)

set(ORT_ROOT ${CMAKE_BINARY_DIR}/onnxruntime-win-x64-1.31.0)
set(ORT_DLL ${ORT_ROOT}/lib/onnxruntime.dll)
if(NOT EXISTS ${ORT_ROOT}/include/onnxruntime_c_api.h)
  set(ORT_ZIP ${CMAKE_BINARY_DIR}/onnxruntime-win-x64-1.31.0.zip)
  message(STATUS "Downloading ONNX Runtime 1.31.0")
  file(DOWNLOAD
    https://github.com/microsoft/onnxruntime/releases/download/v1.31.0/onnxruntime-win-x64-1.31.0.zip
    ${ORT_ZIP}
    SHOW_PROGRESS
    STATUS ORT_STATUS)
  list(GET ORT_STATUS 0 ORT_CODE)
  if(NOT ORT_CODE EQUAL 0)
    list(GET ORT_STATUS 1 ORT_MESSAGE)
    message(FATAL_ERROR "ONNX Runtime download failed: ${ORT_MESSAGE}")
  endif()
  file(ARCHIVE_EXTRACT INPUT ${ORT_ZIP} DESTINATION ${CMAKE_BINARY_DIR})
endif()

set(DR_DIR ${CMAKE_BINARY_DIR}/dr_libs)
file(MAKE_DIRECTORY ${DR_DIR})
foreach(DR_HEADER dr_wav.h dr_mp3.h dr_flac.h)
  if(NOT EXISTS ${DR_DIR}/${DR_HEADER})
    file(DOWNLOAD
      https://raw.githubusercontent.com/mackron/dr_libs/b4264c5c1f61c1d460aced4cf4d8a8dec197ca48/${DR_HEADER}
      ${DR_DIR}/${DR_HEADER}
      STATUS DR_STATUS)
    list(GET DR_STATUS 0 DR_CODE)
    if(NOT DR_CODE EQUAL 0)
      message(FATAL_ERROR "Failed to download ${DR_HEADER}")
    endif()
  endif()
endforeach()

set(MINIAUDIO_DIR ${CMAKE_BINARY_DIR}/miniaudio)
set(MINIAUDIO_H ${MINIAUDIO_DIR}/miniaudio.h)
if(NOT EXISTS ${MINIAUDIO_H})
  file(MAKE_DIRECTORY ${MINIAUDIO_DIR})
  file(DOWNLOAD
    https://raw.githubusercontent.com/mackron/miniaudio/0.11.21/miniaudio.h
    ${MINIAUDIO_H}
    STATUS MINIAUDIO_STATUS)
  list(GET MINIAUDIO_STATUS 0 MINIAUDIO_CODE)
  if(NOT MINIAUDIO_CODE EQUAL 0)
    message(FATAL_ERROR "Failed to download miniaudio")
  endif()
endif()

set(IMGUI_DIR ${CMAKE_BINARY_DIR}/imgui-1.91.9b)
if(NOT EXISTS ${IMGUI_DIR}/imgui.h)
  set(IMGUI_ZIP ${CMAKE_BINARY_DIR}/imgui-1.91.9b.zip)
  message(STATUS "Downloading Dear ImGui 1.91.9b")
  file(DOWNLOAD
    https://github.com/ocornut/imgui/archive/refs/tags/v1.91.9b.tar.gz
    ${IMGUI_ZIP}
    STATUS IMGUI_STATUS)
  list(GET IMGUI_STATUS 0 IMGUI_CODE)
  if(NOT IMGUI_CODE EQUAL 0)
    message(FATAL_ERROR "Failed to download Dear ImGui")
  endif()
  file(ARCHIVE_EXTRACT INPUT ${IMGUI_ZIP} DESTINATION ${CMAKE_BINARY_DIR})
endif()

add_library(bt_stem STATIC
  src/audio_io.cpp
  src/separator.cpp
  src/onnx_model.cpp
  src/basic_pitch.cpp
  src/session.cpp
  src/time_stretch.cpp
  src/player.cpp
  src/beat_track.cpp
  src/project.cpp)
target_include_directories(bt_stem PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_include_directories(bt_stem PRIVATE
  ${DR_DIR}
  ${MINIAUDIO_DIR}
  ${ORT_ROOT}/include
  ${soxr_SOURCE_DIR}/src
  ${soxr_BINARY_DIR})
target_compile_definitions(bt_stem PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(bt_stem PRIVATE /W4 /permissive- /utf-8)
target_link_libraries(bt_stem PUBLIC soxr ${ORT_ROOT}/lib/onnxruntime.lib)
target_link_libraries(bt_stem PRIVATE bcrypt)

set(BT_REPO_ROOT ${CMAKE_SOURCE_DIR}/..)

function(bt_copy_runtime target)
  add_custom_command(TARGET ${target} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${ORT_DLL} $<TARGET_FILE_DIR:${target}>
    VERBATIM)
endfunction()

function(bt_copy_assets target)
  add_custom_command(TARGET ${target} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory $<TARGET_FILE_DIR:${target}>/models
    COMMAND ${CMAKE_COMMAND} -E make_directory $<TARGET_FILE_DIR:${target}>/fonts
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      ${BT_REPO_ROOT}/models/htdemucs_6s.onnx
      $<TARGET_FILE_DIR:${target}>/models/htdemucs_6s.onnx
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      ${BT_REPO_ROOT}/models/nmp.onnx
      $<TARGET_FILE_DIR:${target}>/models/nmp.onnx
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
      ${BT_REPO_ROOT}/fonts/sarasa-gothic-tc-nerd-merged.ttf
      $<TARGET_FILE_DIR:${target}>/fonts/sarasa-gothic-tc-nerd-merged.ttf
    VERBATIM)
endfunction()

add_executable(bt-separate apps/bt_separate/main.cpp)
target_link_libraries(bt-separate PRIVATE bt_stem)
target_compile_options(bt-separate PRIVATE /W4 /permissive- /utf-8)
bt_copy_runtime(bt-separate)

add_library(bt_imgui STATIC
  ${IMGUI_DIR}/imgui.cpp
  ${IMGUI_DIR}/imgui_draw.cpp
  ${IMGUI_DIR}/imgui_tables.cpp
  ${IMGUI_DIR}/imgui_widgets.cpp
  ${IMGUI_DIR}/backends/imgui_impl_win32.cpp
  ${IMGUI_DIR}/backends/imgui_impl_dx11.cpp)
target_include_directories(bt_imgui PUBLIC ${IMGUI_DIR} ${IMGUI_DIR}/backends)
target_compile_definitions(bt_imgui PUBLIC UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(bt_imgui PRIVATE /W3 /utf-8)

add_executable(bt-app WIN32 apps/bt_app/main.cpp apps/bt_app/ui.cpp apps/bt_app/midi_view.cpp apps/bt_app/notes.cpp apps/bt_app/beats.cpp apps/bt_app/monitor.cpp)
target_link_libraries(bt-app PRIVATE bt_stem bt_imgui d3d11 dxgi d3dcompiler ole32 uuid shell32 psapi)
target_compile_definitions(bt-app PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(bt-app PRIVATE /W4 /permissive- /utf-8)
bt_copy_runtime(bt-app)
bt_copy_assets(bt-app)

add_executable(bt_stem_tests
  tests/test_host.cpp
  tests/test_session.cpp
  tests/test_player.cpp
  tests/test_notes.cpp
  tests/test_beats.cpp
  tests/test_projects.cpp
  tests/test_runtime.cpp
  apps/bt_app/notes.cpp
  apps/bt_app/beats.cpp
  apps/bt_app/monitor.cpp
  apps/bt_app/ui.cpp
  apps/bt_app/midi_view.cpp)
target_include_directories(bt_stem_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/apps/bt_app)
target_link_libraries(bt_stem_tests PRIVATE bt_stem bt_imgui Catch2::Catch2WithMain d3d11 dxgi d3dcompiler ole32 uuid shell32 psapi)
target_compile_definitions(bt_stem_tests PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(bt_stem_tests PRIVATE /W4 /permissive- /utf-8)
bt_copy_runtime(bt_stem_tests)

include(CTest)
include(${catch2_SOURCE_DIR}/extras/Catch.cmake)
catch_discover_tests(bt_stem_tests)
