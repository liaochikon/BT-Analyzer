# Copy repo-root (or legacy build-dir) CUDA EP DLLs next to the app.
# Prefer <repo>/cuda-ep from fetch-cuda.bat; fall back to <build>/cuda-ep.
if(NOT DEFINED BT_REPO_ROOT OR NOT DEFINED BT_DST_DIR OR NOT DEFINED BT_BUILD_DIR)
  message(FATAL_ERROR "copy_cuda_ep.cmake needs BT_REPO_ROOT, BT_BUILD_DIR, BT_DST_DIR")
endif()

set(src "")
if(EXISTS "${BT_REPO_ROOT}/cuda-ep/onnxruntime_providers_cuda.dll")
  set(src "${BT_REPO_ROOT}/cuda-ep")
elseif(EXISTS "${BT_BUILD_DIR}/cuda-ep/onnxruntime_providers_cuda.dll")
  set(src "${BT_BUILD_DIR}/cuda-ep")
endif()

if(src STREQUAL "")
  return()
endif()

file(GLOB cuda_dlls "${src}/*.dll")
foreach(dll IN LISTS cuda_dlls)
  get_filename_component(name "${dll}" NAME)
  execute_process(COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${dll}" "${BT_DST_DIR}/${name}")
endforeach()
