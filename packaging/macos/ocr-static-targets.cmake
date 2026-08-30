# CMake 默认不会把项目注入文件传给 try_compile。把本文件显式列入平台
# 变量传播清单，确保临时工程也能恢复下方锁定依赖目标。
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES CMAKE_PROJECT_INCLUDE_BEFORE)
list(REMOVE_DUPLICATES CMAKE_TRY_COMPILE_PLATFORM_VARIABLES)

set(_partyops_ocr_try_compile FALSE)
if(CMAKE_BINARY_DIR MATCHES "CMakeScratch[/\\\\]TryCompile-")
  set(_partyops_ocr_try_compile TRUE)
endif()

# libtiff 的静态导出目标会引用 CMath::CMath，但该辅助目标不会随安装结果
# 一同导出。只在正常主工程补齐该目标；try_compile 会把主工程的 CMath
# 导入目标写入临时 Targets 文件，提前重复创建反而会造成名称冲突。
if(NOT _partyops_ocr_try_compile AND NOT TARGET CMath::CMath)
  add_library(CMath::CMath INTERFACE IMPORTED GLOBAL)
  set_property(TARGET CMath::CMath PROPERTY INTERFACE_LINK_LIBRARIES m)
endif()

# Leptonica 的静态导出配置只记录目标名，不会主动恢复 ZLIB/PNG/JPEG/TIFF
# 目标；Tesseract 主工程与其 try_compile 临时工程因此都必须从同一锁定
# 前缀恢复这些目标。每个目标均带存在性保护，避免覆盖 CMake 已加载目标。
set(_partyops_ocr_prefix "$ENV{PARTYOPS_OCR_PREFIX}")
if(_partyops_ocr_prefix STREQUAL "")
  message(FATAL_ERROR "PARTYOPS_OCR_PREFIX is required for the OCR build")
endif()

function(_partyops_import_ocr_archive target_name archive_name)
  set(archive_path "${_partyops_ocr_prefix}/lib/${archive_name}")
  if(NOT EXISTS "${archive_path}")
    message(FATAL_ERROR "Locked OCR archive is missing: ${archive_path}")
  endif()
  if(NOT TARGET "${target_name}")
    add_library("${target_name}" STATIC IMPORTED GLOBAL)
    set_target_properties(
      "${target_name}"
      PROPERTIES
        IMPORTED_LOCATION "${archive_path}"
        INTERFACE_INCLUDE_DIRECTORIES "${_partyops_ocr_prefix}/include"
    )
  endif()
endfunction()

_partyops_import_ocr_archive(ZLIB::ZLIB libz.a)
_partyops_import_ocr_archive(PNG::PNG libpng16.a)
_partyops_import_ocr_archive(JPEG::JPEG libjpeg.a)
_partyops_import_ocr_archive(TIFF::TIFF libtiff.a)
set_property(TARGET PNG::PNG PROPERTY INTERFACE_LINK_LIBRARIES ZLIB::ZLIB)
set_property(
  TARGET TIFF::TIFF
  PROPERTY INTERFACE_LINK_LIBRARIES "JPEG::JPEG;ZLIB::ZLIB;CMath::CMath"
)
