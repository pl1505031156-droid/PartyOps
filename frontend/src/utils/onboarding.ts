export interface LoginFormValues {
  username: string;
  password: string;
  displayName: string;
}

export type LoginFieldErrors = Partial<Record<keyof LoginFormValues, string>>;

export function validateLoginForm(
  form: LoginFormValues,
  configured: boolean,
): LoginFieldErrors {
  const errors: LoginFieldErrors = {};
  const username = form.username.trim();
  const displayName = form.displayName.trim();

  if (!configured) {
    if (!displayName) errors.displayName = "请填写首位管理员姓名";
    else if (displayName.length < 2) errors.displayName = "管理员姓名至少填写 2 个字";
  }
  if (!username) errors.username = "请填写用户名";
  else if (!/^[A-Za-z0-9_.-]+$/.test(username)) {
    errors.username = "用户名只能使用英文字母、数字、点、短横线或下划线";
  }
  if (!form.password) errors.password = "请填写密码";
  else if (!configured && form.password.length < 8) errors.password = "密码至少需要 8 个字符";
  return errors;
}
