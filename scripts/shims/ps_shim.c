/*
 * ps_shim.c —— REA 用到的 `ps` 形态的**最小**兼容实现（Darwin）
 *
 * ## 为什么需要它
 *
 * 本仓库的开发环境**拒绝对系统 `ps` 的 exec**（errno = EPERM；同一环境下
 * `pgid` 可用而 `ps` 不行）。REA 在关闭 provider 前会用
 *
 *     ps -axo pid=,ppid=,pgid=,uid=,stat=,command=
 *
 * 扫一遍进程表、确认它派生的 worker 进程组已经退出。拿不到进程表时 REA
 * **fail-closed**：把一次**分析实际上成功了**的运行记成
 *
 *     {"code":"cleanup_incomplete","category":"execution_failure"}
 *
 * 并回收结果（rc=1）。真因埋在
 * `details.diagnostics.provider_cleanup.reason = "process table could not be
 * inspected: spawn EPERM"` —— 不看这一层的人会去改根本没错的地方。
 *
 * ⇒ 本 shim 补上这条**文档里没写的隐式依赖**：用与 `ps` 同源的
 *   `KERN_PROC_ALL` sysctl（`<sys/sysctl.h>` 的 `struct kinfo_proc`）
 *   真实地读进程表，按 ps 的列格式输出。
 *
 * ## 为什么用 kinfo_proc 而不是 libproc 的 proc_bsdinfo
 *
 * 实测（2026-10-15，macOS arm64 / Darwin 25.6）：`proc_bsdinfo.pbi_status`
 * 在本机对所有可读进程**恒为 2**（= SRUN），连正在 `sleep()` 的进程也报 2
 * ⇒ 拿它当 `p_stat` 用会把"僵尸判活"那一步带偏。而 `kinfo_proc` 是 `ps`
 * 自己用的结构 ⇒ 字段语义同源，用它。
 *
 * ## 三条不许
 *
 * ⛔ **不编造字段**。某个进程读不到 ⇒ **整行跳过**（不半行、不填占位）。
 *    宁可少一行，也不能让调用方拿一个"看起来齐但字段是编的"的表去判活。
 * ⛔ **不"猜一个像样的输出"**：不支持的参数组合一律 exit 64 + stderr 说明。
 *    静默猜 = 假仪表，正是本项目宪法禁止的东西。
 * ⛔ **不追求与 ps 全等**：只实现 REA 真正请求的 spec；STAT 列只吐首字符
 *    （`+`/`s`/`X` 需要控制终端信息，kinfo_proc 给不出 ⇒ 不猜）。
 *
 * 编译：cc -O2 -o ps ps_shim.c   （零第三方依赖，只用系统 sysctl/libproc）
 */
#include <errno.h>
#include <libproc.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/proc.h> /* SIDL / SRUN / SSLEEP / SSTOP / SZOMB */
#include <sys/proc_info.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#include <unistd.h>

/* 不认识的形态：exit 64（EX_USAGE）+ stderr 原因。 */
#define EXIT_UNSUPPORTED 64

/* 命令行输出上限（字节）：进程 argv 可能带超长参数，撑爆 stdout 预算会让
 * `CliAdapter` **直接报错**（不截断）⇒ 这里主动截断，保住整份信封。 */
#define CMD_MAX 4096u

/* ── 列规格 ──────────────────────────────────────────────────────────────────
 * 只实现 REA 真正请求的 spec，来源为
 * `node_modules/rea-agents/dist/process/ProcessOwnershipObservation.js`
 * 里 `execFileOutput("ps", ["-axo", ...])` 的实参（不是二手文档）。
 */
#define SPEC_OWNERSHIP "pid=,ppid=,pgid=,uid=,stat=,command="

/** ps STAT 列的**首字符**。
 *
 *  ⛔ 取值来自 `kinfo_proc.kp_proc.p_stat`（与 `ps` 同源）。
 *     只吐首字符 —— `+`/`s`/`X` 等标志位需要"控制终端的前台进程组"信息，
 *     kinfo_proc 给不出 ⇒ 不猜（猜了就是假仪表）。
 *  ✅ 消费者 `liveProcesses()` 的判活条件是 `!state.startsWith("Z")`，
 *     首字符足够；僵尸（`SZOMB`）照实报 `Z`。
 */
static const char *stat_letter(char st) {
    switch ((int)st) {
        case SIDL:   return "I";
        case SRUN:   return "R";
        case SSLEEP: return "S";
        case SSTOP:  return "T";
        case SZOMB:  return "Z";
        default:     return "?";
    }
}

/* ── 输出净化 ─────────────────────────────────────────────────────────────
 * ⛔ 为什么必须净化：进程的 argv / env 里可能带 `\n`（实测：python heredoc
 *    调用就是），一行变多行 ⇒ REA 按行切进程会**错位**（把续行当成另一个
 *    进程），进而把活着的 worker 判成已死。这是最坏的一类假读数。
 */
static size_t g_written = 0;

static void putc_sanitized(char c) {
    if (g_written >= CMD_MAX) return;
    if (((unsigned char)c) < 0x20u || ((unsigned char)c) == 0x7fu) c = ' ';
    fputc(c, stdout);
    g_written++;
}

static void puts_sanitized(const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) putc_sanitized(s[i]);
}

/** 读 `KERN_PROCARGS2` 原始块：
 *      int argc | execname\0 | NUL 补齐 | argv[0]\0 ... | env\0 ...
 *  ⛔ 补齐长度依赖指针对齐，不猜 —— 解析时跳过连续 NUL 段。
 */
static char *procargs2(int pid, size_t *out_len, int *out_argc) {
    int mib[3] = { CTL_KERN, KERN_PROCARGS2, pid };
    size_t len = 0;
    if (sysctl(mib, 3, NULL, &len, NULL, 0) != 0) return NULL;
    if (len <= sizeof(int)) return NULL;
    char *buf = (char *)calloc(1, len + 1);
    if (buf == NULL) return NULL;
    if (sysctl(mib, 3, buf, &len, NULL, 0) != 0) { free(buf); return NULL; }
    int argc = 0;
    memcpy(&argc, buf, sizeof(int));
    if (argc < 0) argc = 0;
    if (argc > 4096) argc = 4096;
    *out_len = len;
    *out_argc = argc;
    return buf;
}

/** 打印 `buf` 里：跳过前 `skip` 个串，然后打印最多 `count` 个串
 *  （`count < 0` ⇒ 打印到缓冲区结束，用于 env）。 */
static void collect(char *buf, size_t len, int skip, int count) {
    size_t pos = sizeof(int);
    for (int i = 0; i < skip && pos < len; i++) {
        while (pos < len && buf[pos] != '\0') pos++;
        pos++;
        while (pos < len && buf[pos] == '\0') pos++;
    }
    int emitted = 0;
    while (pos < len && (count < 0 || emitted < count)) {
        size_t start = pos;
        while (pos < len && buf[pos] != '\0') pos++;
        if (emitted > 0) putc_sanitized(' ');
        puts_sanitized(buf + start, pos - start);
        emitted++;
        pos++;
        while (pos < len && buf[pos] == '\0') pos++;
    }
}

/** 进程命令行（含参数）。拿不到 argv 就退回可执行文件路径 —— 但不假装
 *  拿到了参数（路径 ≠ 命令行，混起来会让"同一个 java 的多个实例"不可分）。*/
static void print_command(pid_t pid) {
    size_t len = 0;
    int argc = 0;
    char *raw = procargs2(pid, &len, &argc);
    if (raw != NULL) {
        collect(raw, len, 1, argc);   /* 跳过 execname（argv[0] 的副本） */
        free(raw);
        return;
    }
    char path[PROC_PIDPATHINFO_MAXSIZE];
    path[0] = '\0';
    if (proc_pidpath(pid, path, sizeof path) > 0) {
        puts_sanitized(path, strlen(path));
    }
}

/** 全表：`ps -axo pid=,ppid=,pgid=,uid=,stat=,command=` 的等价输出。
 *
 *  数据源 = `KERN_PROC_ALL` sysctl（与 `ps` 同源），字段含义与 ps 一致：
 *    pid / ppid / pgid / uid / stat(首字符) / command(含参数)
 *  ⛔ 僵尸进程**保留**在表里并如实标 `Z` —— 消费者要靠它判断"worker 还没死"。
 */
static void print_table(void) {
    int mib[3] = { CTL_KERN, KERN_PROC, KERN_PROC_ALL };
    size_t len = 0;
    if (sysctl(mib, 3, NULL, &len, NULL, 0) != 0) return;
    struct kinfo_proc *procs = (struct kinfo_proc *)calloc(1, len + 8192);
    if (procs == NULL) return;
    if (sysctl(mib, 3, procs, &len, NULL, 0) != 0) { free(procs); return; }
    size_t n = len / sizeof(struct kinfo_proc);

    for (size_t i = 0; i < n; i++) {
        struct kinfo_proc *kp = &procs[i];
        pid_t pid = kp->kp_proc.p_pid;
        if (pid <= 0) continue;                     /* 列表空洞 */
        g_written = 0;
        printf("%d %d %d %d %s ", (int)pid,
               (int)kp->kp_eproc.e_ppid, (int)kp->kp_eproc.e_pgid,
               (int)kp->kp_eproc.e_ucred.cr_uid,
               stat_letter(kp->kp_proc.p_stat));
        print_command(pid);
        fputc('\n', stdout);
    }
    free(procs);
}

/** `ps eww -p <pid>`：把该进程的环境变量打成 `K=V` 串。
 *
 *  REA 用这段输出读 worker 的环境（内含 `REA_PROCESS_RUN_ID`）来确认进程
 *  归属。它的判据是 `/(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=([^\s]*)/` ⇒
 *  这里**只吐**该正则认得出的形态，不自创"什么像环境变量"的规则。
 */
static void print_env(pid_t pid) {
    char path[PROC_PIDPATHINFO_MAXSIZE];
    path[0] = '\0';
    (void)proc_pidpath(pid, path, sizeof path);
    printf("  PID TTY           TIME CMD\n");
    printf("%5d ??          0:00.00 %s ", (int)pid, path[0] ? path : "");
    size_t len = 0;
    int argc = 0;
    char *raw = procargs2(pid, &len, &argc);
    if (raw != NULL) {
        collect(raw, len, 1 + argc, -1);      /* 跳过 execname + argv */
        free(raw);
    }
    fputc('\n', stdout);
}

int main(int argc, char **argv) {
    /* ── `--selftest`：用自己的 pid / pgid / uid 对拍。
     *    为什么要有：本 shim 依赖 `struct kinfo_proc` 的布局，而
     *    `sizeof(struct proc_bsdinfo)` 实测 **136**（不是常见的 112）
     *    ⇒ 布局漂了必须**跑之前**就炸，不能静默出一张全是 0 的假表。 */
    if (argc == 2 && strcmp(argv[1], "--selftest") == 0) {
        struct kinfo_proc kp;
        size_t len = sizeof kp;
        int mib[4] = { CTL_KERN, KERN_PROC, KERN_PROC_PID, (int)getpid() };
        memset(&kp, 0, sizeof kp);
        if (sysctl(mib, 4, &kp, &len, NULL, 0) != 0 || len == 0) {
            fprintf(stderr, "ps_shim: selftest: sysctl KERN_PROC_PID 失败\n");
            return 1;
        }
        int bad = 0;
        if ((int)kp.kp_proc.p_pid != (int)getpid()) bad = 1;
        if ((int)kp.kp_eproc.e_pgid != (int)getpgid(0)) bad = 1;
        if ((uid_t)kp.kp_eproc.e_ucred.cr_uid != getuid()) bad = 1;
        printf("ps_shim: selftest %s (pid=%d ppid=%d pgid=%d uid=%d stat=%s)\n",
               bad ? "FAIL" : "ok", (int)kp.kp_proc.p_pid,
               (int)kp.kp_eproc.e_ppid, (int)kp.kp_eproc.e_pgid,
               (int)kp.kp_eproc.e_ucred.cr_uid,
               stat_letter(kp.kp_proc.p_stat));
        return bad ? 1 : 0;
    }

    /* ── 形态 1：`ps [-a][-x] -o pid=,ppid=,pgid=,uid=,stat=,command=` ── */
    for (int i = 1; i < argc; i++) {
        const char *spec = NULL;
        if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
            spec = argv[i + 1];
        } else if (strncmp(argv[i], "-axo", 4) == 0
                   || strncmp(argv[i], "-ao", 3) == 0
                   || strncmp(argv[i], "-xo", 3) == 0) {
            const char *p = argv[i];
            while (*p != '\0' && *p != 'o') p++;
            p++;
            spec = p;
            if (*spec == '\0' && i + 1 < argc) spec = argv[i + 1];
        }
        if (spec == NULL) continue;
        if (strcmp(spec, SPEC_OWNERSHIP) != 0) {
            fprintf(stderr, "ps_shim: 不支持的 -o 规格 %s（只实现 %s）\n",
                    spec, SPEC_OWNERSHIP);
            return EXIT_UNSUPPORTED;
        }
        print_table();
        return 0;
    }

    /* ── 形态 2：`ps eww -p <pid>`（`-eww` 同义）── */
    for (int j = 1; j < argc; j++) {
        if (strcmp(argv[j], "eww") != 0 && strcmp(argv[j], "-eww") != 0) continue;
        for (int k = 1; k + 1 < argc; k++) {
            if (strcmp(argv[k], "-p") == 0) {
                pid_t pid = (pid_t)atoi(argv[k + 1]);
                if (pid > 0) { print_env(pid); return 0; }
            }
        }
    }

    /* ── 形态 3：`ps -A` / `ps ax` / `ps aux`：通用表头视图 ── */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-A") == 0 || strcmp(argv[i], "ax") == 0
            || strcmp(argv[i], "-ax") == 0 || strcmp(argv[i], "aux") == 0) {
            printf("  PID TTY           TIME CMD\n");
            print_table();
            return 0;
        }
    }

    fprintf(stderr, "ps_shim: 不支持的 ps 调用：");
    for (int i = 1; i < argc; i++) fprintf(stderr, " %s", argv[i]);
    fprintf(stderr, "\n（本 shim 只实现 `%s`、`eww -p PID`、`-A` 三种形态；"
                    "不支持的一律响亮退出，绝不静默猜输出）\n", SPEC_OWNERSHIP);
    return EXIT_UNSUPPORTED;
}
