/*
 * PartyOps macOS LaunchServices 原生入口。
 *
 * Finder 不会保留标准输出；如果 PyInstaller 引导器在 Python 代码执行前
 * 失败，launcher.log 也无从创建。这个很薄的 Mach-O 入口先写入独立探针，
 * 再把参数原样交给冻结的桌面启动器，使“双击无反应”始终留下证据。
 */

#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pwd.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static const char *resolve_home(void) {
    const char *home = getenv("HOME");
    if (home != NULL && home[0] == '/') {
        return home;
    }
    const struct passwd *entry = getpwuid(getuid());
    if (entry != NULL && entry->pw_dir != NULL && entry->pw_dir[0] == '/') {
        return entry->pw_dir;
    }
    return NULL;
}

static bool ensure_directory(const char *path, mode_t mode) {
    struct stat metadata;
    if (lstat(path, &metadata) == 0) {
        return S_ISDIR(metadata.st_mode) && !S_ISLNK(metadata.st_mode);
    }
    if (errno != ENOENT) {
        return false;
    }
    return mkdir(path, mode) == 0;
}

static bool partyops_log_path(const char *name, char *output, size_t output_size) {
    const char *home = resolve_home();
    if (home == NULL) {
        return false;
    }

    char library[PATH_MAX];
    char logs[PATH_MAX];
    char partyops[PATH_MAX];
    if (snprintf(library, sizeof(library), "%s/Library", home) >= (int)sizeof(library) ||
        snprintf(logs, sizeof(logs), "%s/Logs", library) >= (int)sizeof(logs) ||
        snprintf(partyops, sizeof(partyops), "%s/PartyOps", logs) >= (int)sizeof(partyops) ||
        snprintf(output, output_size, "%s/%s", partyops, name) >= (int)output_size) {
        return false;
    }
    if (!ensure_directory(library, 0700) || !ensure_directory(logs, 0700) ||
        !ensure_directory(partyops, 0700)) {
        return false;
    }
    return true;
}

static void append_probe(const char *format, ...) {
    char log_path[PATH_MAX];
    if (!partyops_log_path("launch-probe.log", log_path, sizeof(log_path))) {
        return;
    }

    FILE *handle = fopen(log_path, "a");
    if (handle == NULL) {
        return;
    }
    (void)fchmod(fileno(handle), 0600);
    const time_t now = time(NULL);
    struct tm local_time;
    char timestamp[32] = "unknown-time";
    if (localtime_r(&now, &local_time) != NULL) {
        (void)strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", &local_time);
    }
    (void)fprintf(handle, "%s pid=%ld uid=%ld ", timestamp, (long)getpid(), (long)getuid());
    va_list arguments;
    va_start(arguments, format);
    (void)vfprintf(handle, format, arguments);
    va_end(arguments);
    (void)fputc('\n', handle);
    (void)fclose(handle);
}

static void show_fatal_alert(void) {
    const char *script =
        "display alert \"党建智办启动失败\" message "
        "\"macOS 原生入口无法启动桌面组件。请把 ~/Library/Logs/PartyOps/launch-probe.log 发给技术支持。\" "
        "as critical buttons {\"知道了\"} default button \"知道了\"";
    execl("/usr/bin/osascript", "osascript", "-e", script, (char *)NULL);
}

int main(int argc, char *argv[]) {
    uint32_t executable_size = PATH_MAX;
    char executable[PATH_MAX];
    if (_NSGetExecutablePath(executable, &executable_size) != 0) {
        append_probe("status=wrapper-path-too-long");
        show_fatal_alert();
        return 126;
    }

    char resolved[PATH_MAX];
    if (realpath(executable, resolved) == NULL) {
        append_probe("status=wrapper-realpath-failed errno=%d", errno);
        show_fatal_alert();
        return 126;
    }
    char directory_input[PATH_MAX];
    (void)snprintf(directory_input, sizeof(directory_input), "%s", resolved);
    const char *directory = dirname(directory_input);
    char target[PATH_MAX];
    if (snprintf(target, sizeof(target), "%s/partyops-desktop-bin", directory) >=
        (int)sizeof(target)) {
        append_probe("status=target-path-too-long");
        show_fatal_alert();
        return 126;
    }

    struct stat target_metadata;
    if (lstat(target, &target_metadata) != 0 || !S_ISREG(target_metadata.st_mode) ||
        access(target, X_OK) != 0) {
        append_probe("status=desktop-resource-invalid target=partyops-desktop-bin errno=%d", errno);
        show_fatal_alert();
        return 126;
    }

    struct utsname system_info;
    const char *architecture = "unknown";
    if (uname(&system_info) == 0) {
        architecture = system_info.machine;
    }
    append_probe(
        "status=launchservices-entered architecture=%s target=partyops-desktop-bin "
        "target_size=%lld argc=%d",
        architecture,
        (long long)target_metadata.st_size,
        argc
    );

    char **child_argv = calloc((size_t)argc + 1U, sizeof(char *));
    if (child_argv == NULL) {
        append_probe("status=argument-allocation-failed errno=%d", errno);
        show_fatal_alert();
        return 126;
    }
    child_argv[0] = target;
    for (int index = 1; index < argc; ++index) {
        child_argv[index] = argv[index];
    }
    child_argv[argc] = NULL;

    const pid_t child = fork();
    if (child < 0) {
        append_probe("status=desktop-fork-failed errno=%d", errno);
        free(child_argv);
        show_fatal_alert();
        return 126;
    }
    if (child == 0) {
        char stderr_path[PATH_MAX];
        if (partyops_log_path("launch-stderr.log", stderr_path, sizeof(stderr_path))) {
            const int stderr_fd = open(stderr_path, O_WRONLY | O_CREAT | O_APPEND, 0600);
            if (stderr_fd >= 0) {
                (void)fchmod(stderr_fd, 0600);
                (void)dup2(stderr_fd, STDERR_FILENO);
                (void)dup2(stderr_fd, STDOUT_FILENO);
                if (stderr_fd > STDERR_FILENO) {
                    (void)close(stderr_fd);
                }
            }
        }
        execv(target, child_argv);
        (void)dprintf(STDERR_FILENO, "[MACOS_DESKTOP_EXEC_FAILED] errno=%d\n", errno);
        _exit(126);
    }

    append_probe("status=desktop-child-started child_pid=%ld", (long)child);
    int child_status = 0;
    pid_t waited;
    do {
        waited = waitpid(child, &child_status, 0);
    } while (waited < 0 && errno == EINTR);
    free(child_argv);
    if (waited < 0) {
        append_probe("status=desktop-wait-failed child_pid=%ld errno=%d", (long)child, errno);
        show_fatal_alert();
        return 126;
    }
    if (WIFEXITED(child_status)) {
        const int exit_code = WEXITSTATUS(child_status);
        append_probe("status=desktop-child-exited child_pid=%ld exit_code=%d", (long)child, exit_code);
        if (exit_code != 0) {
            show_fatal_alert();
        }
        return exit_code;
    }
    if (WIFSIGNALED(child_status)) {
        const int signal_number = WTERMSIG(child_status);
        append_probe("status=desktop-child-signaled child_pid=%ld signal=%d", (long)child, signal_number);
        show_fatal_alert();
        return 128 + signal_number;
    }
    append_probe("status=desktop-child-unknown child_pid=%ld raw_status=%d", (long)child, child_status);
    show_fatal_alert();
    return 126;
}
