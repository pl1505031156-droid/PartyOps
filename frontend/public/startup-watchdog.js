(function () {
  "use strict";

  var finished = false;

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (character) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[character];
    });
  }

  function render(code, detail) {
    if (finished || window.__PARTYOPS_STARTED__) return;
    finished = true;
    var root = document.getElementById("app");
    if (!root) return;
    root.innerHTML =
      '<main style="max-width:680px;margin:64px auto;padding:32px;font-family:system-ui,Microsoft YaHei,sans-serif;line-height:1.75;color:#292724;background:#fbf8f1;border:1px solid #d9d0c3;border-top:4px solid #b42318">' +
      '<p style="margin:0 0 10px;color:#b42318;font-size:12px;letter-spacing:.12em">PARTYOPS RECOVERY</p>' +
      '<h1 style="margin:0;font-size:26px">页面资源未能完成启动</h1>' +
      '<p style="color:#5f5952">' + escapeHtml(detail) + '</p>' +
      '<p style="color:#8a8178;font-size:12px">诊断代码：' + escapeHtml(code) + '</p>' +
      '<button type="button" onclick="window.location.reload()" style="height:42px;padding:0 22px;color:#fff;background:#b42318;border:0;cursor:pointer">重新载入系统</button>' +
      '</main>';
  }

  window.addEventListener("partyops:started", function () {
    finished = true;
  });
  window.addEventListener("error", function (event) {
    var target = event && event.target;
    if (target && (target.tagName === "SCRIPT" || target.tagName === "LINK")) {
      render("FRONTEND_ASSET_LOAD_FAILED", "升级后的页面脚本或样式文件缺失，系统已停止显示空白页。请重启 PartyOps 服务后重新载入。");
    }
  }, true);
  window.addEventListener("unhandledrejection", function () {
    render("FRONTEND_STARTUP_REJECTED", "页面启动过程发生未处理异常，系统已保留可见诊断，业务数据没有因此改变。");
  });
  window.setTimeout(function () {
    render("FRONTEND_STARTUP_TIMEOUT", "页面在 20 秒内未完成启动。常见原因是旧版资源缓存或安装包页面文件不完整。");
  }, 20000);
}());
