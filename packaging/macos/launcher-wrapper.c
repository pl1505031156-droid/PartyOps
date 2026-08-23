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

static void sanitize_child_environment(void) {
    /* Finder、旧 PyInstaller 父进程或终端启动均不得把冻结运行时状态传给新进程。 */
    static const char *variables[] = {
        "_PYI_ARCHIVE_FILE",
        "_PYI_APPLICATION_HOME_DIR",
        "_PYI_PARENT_PROCESS_LEVEL",
        "_PYI_SPLASH_IPC",
        "_MEIPASS2",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONEXECUTABLE",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_INSERT_LIBRARIES",
        "LD_LIBRARY_PATH",
        NULL,
    };
    for (size_t index = 0; variables[index] != NULL; ++index) {
        (void)unsetenv(variables[index]);
    }
    /*
     * PyInstaller 官方约定：从另一个程序重新启动冻结入口时显式要求
     * bootloader 建立全新的运行时层级。用户日志中的 255 发生在 Python
     * 代码和 launcher.log 之前，正是 bootloader 继承态必须被排除的阶段。
     */
    (void)setenv("PYINSTALLER_RESET_ENVIRONMENT", "1", 1);
    (void)setenv("PYTHONUTF8", "1", 1);
    (void)setenv("LANG", "zh_CN.UTF-8", 1);
    (void)setenv("LC_CTYPE", "UTF-8", 1);
    (void)setenv("PATH", "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);
}

static void append_stderr_tail(void) {
    char path[PATH_MAX];
    if (!partyops_log_path("launch-stderr.log", path, sizeof(path))) {
        return;
    }
    FILE *handle = fopen(path, "rb");
    if (handle == NULL) {
        append_probe("status=desktop-stderr-missing errno=%d", errno);
        return;
    }
    if (fseek(handle, 0L, SEEK_END) != 0) {
        (void)fclose(handle);
        return;
    }
    const long size = ftell(handle);
    if (size < 0L) {
        (void)fclose(handle);
        return;
    }
    const long start = size > 4096L ? size - 4096L : 0L;
    if (fseek(handle, start, SEEK_SET) != 0) {
        (void)fclose(handle);
        return;
    }
    char buffer[4097];
    const size_t count = fread(buffer, 1U, sizeof(buffer) - 1U, handle);
    (void)fclose(handle);
    for (size_t index = 0; index < count; ++index) {
        const unsigned char value = (unsigned char)buffer[index];
        if (value == '\n' || value == '\r' || value == '\t') {
            buffer[index] = ' ';
        } else if (value < 0x20U || value == 0x7fU) {
            buffer[index] = '?';
        }
    }
    buffer[count] = '\0';
    append_probe(
        "status=desktop-stderr-tail source_bytes=%ld captured_bytes=%zu text=%s",
        size,
        count,
        buffer
    );
}

static void show_fatal_alert(void) {
    const char *script =
        "display alert \"党建智办启动失败\" message "
        "\"macOS 原生入口无法启动桌面组件。请把 ~/Library/Logs/PartyOps/launch-probe.log 和 launch-stderr.log 发给技术支持。\" "
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
    char working_directory[PATH_MAX] = "unknown";
    if (getcwd(working_directory, sizeof(working_directory)) == NULL) {
        (void)snprintf(working_directory, sizeof(working_directory), "unavailable:%d", errno);
    }
    append_probe(
        "status=launchservices-entered architecture=%s os_release=%s "
        "target=partyops-desktop-bin target_size=%lld argc=%d cwd=%s",
        architecture,
        uname(&system_info) == 0 ? system_info.release : "unknown",
        (long long)target_metadata.st_size,
        argc,
        working_directory
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
        if (chdir(directory) != 0) {
            (void)dprintf(
                STDERR_FILENO,
                "[MACOS_DESKTOP_CHDIR_FAILED] errno=%d directory=%s\n",
                errno,
                directory
            );
            _exit(126);
        }
        sanitize_child_environment();
        (void)dprintf(
            STDERR_FILENO,
            "[MACOS_DESKTOP_EXEC] target=partyops-desktop-bin cwd=%s\n",
            directory
        );
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
            append_stderr_tail();
            show_fatal_alert();
        }
        return exit_code;
    }
    if (WIFSIGNALED(child_status)) {
        const int signal_number = WTERMSIG(child_status);
        append_probe("status=desktop-child-signaled child_pid=%ld signal=%d", (long)child, signal_number);
        append_stderr_tail();
        show_fatal_alert();
        return 128 + signal_number;
    }
    append_probe("status=desktop-child-unknown child_pid=%ld raw_status=%d", (long)child, child_status);
    show_fatal_alert();
    return 126;
}
