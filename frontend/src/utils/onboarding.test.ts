import { describe, expect, it } from "vitest";
import { validateLoginForm } from "./onboarding";

describe("首次配置字段级防错", () => {
  it("创建首位管理员时返回可直接理解的中文字段错误", () => {
    expect(validateLoginForm({ username: "中 文", password: "123", displayName: "王" }, false)).toEqual({
      displayName: "管理员姓名至少填写 2 个字",
      username: "用户名只能使用英文字母、数字、点、短横线或下划线",
      password: "密码至少需要 8 个字符",
    });
  });

  it("普通登录不把创建管理员的姓名与密码长度规则强加给旧账号", () => {
    expect(validateLoginForm({ username: "staff", password: "1", displayName: "" }, true)).toEqual({});
  });

  it("空值错误按姓名、用户名、密码顺序稳定返回", () => {
    expect(Object.keys(validateLoginForm({ username: "", password: "", displayName: "" }, false))).toEqual([
      "displayName",
      "username",
      "password",
    ]);
  });
});
