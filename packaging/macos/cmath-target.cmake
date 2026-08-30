# libtiff 的静态导出目标会引用 CMath::CMath，但该辅助目标不会随安装结果
# 一同导出。在加载 TIFFConfig.cmake 前补齐系统数学库目标，避免 Leptonica
# 与 Tesseract 配置阶段因缺少构建机私有 CMake 模块而失败。
if(NOT TARGET CMath::CMath)
  add_library(CMath::CMath INTERFACE IMPORTED GLOBAL)
  set_property(TARGET CMath::CMath PROPERTY INTERFACE_LINK_LIBRARIES m)
endif()
