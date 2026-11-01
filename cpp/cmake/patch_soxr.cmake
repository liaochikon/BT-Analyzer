if(NOT SOXR_DIR)
  message(FATAL_ERROR "SOXR_DIR is not set")
endif()

file(READ "${SOXR_DIR}/CMakeLists.txt" _soxr_text)
string(REPLACE
  "CMAKE_SOURCE_DIR}/cmake/Modules"
  "CMAKE_CURRENT_SOURCE_DIR}/cmake/Modules"
  _soxr_text "${_soxr_text}")
file(WRITE "${SOXR_DIR}/CMakeLists.txt" "${_soxr_text}")
