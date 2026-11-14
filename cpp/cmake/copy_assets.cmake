if(NOT DEFINED BT_SRC_DIR OR NOT DEFINED BT_DST_DIR)
  message(FATAL_ERROR "copy_assets.cmake needs BT_SRC_DIR and BT_DST_DIR")
endif()

set(assets
  models/nmp.onnx
  fonts/sarasa-gothic-tc-nerd-merged.ttf
  locales/zh-Hant.txt
  locales/zh-Hans.txt
  locales/en.txt
  assets/icons/atlas.png
  assets/icons/atlas.json)

foreach(rel IN LISTS assets)
  set(src "${BT_SRC_DIR}/${rel}")
  set(dst "${BT_DST_DIR}/${rel}")
  if(EXISTS "${src}")
    get_filename_component(dir "${dst}" DIRECTORY)
    file(MAKE_DIRECTORY "${dir}")
    execute_process(COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${src}" "${dst}")
  else()
    message(STATUS "Asset not present, skip copy: ${rel}")
  endif()
endforeach()
