/*
 * Valve Oil THR catch, compiled: what `vo_thr_catch.py --early` does, without Python's start-up time.
 *
 * The systemd generator (vo-thr-catch) starts it about a second after the kernel while a THR firmware flash is
 * unfinished. A THR bootloader stays after power-on only if a packet reaches it within its startup window, four to ten
 * seconds after power-on depending on the build, and Python takes about 0.4 s to start on these printers. This catches
 * and holds exactly as vo_thr_catch.py does and writes the same record to CATCH; vo_thr_catch.py (the unit's first
 * step) stops it with SIGTERM once the unit runs. It sends only identity requests, never a start or data packet.
 *
 * The installer compiles it on the printer with the printer's own gcc and installs it at /usr/local/lib/vo_thr_catch;
 * when it can't, the generator starts vo_thr_catch.py --early instead.
 *
 *   --early  catch, and hold an answering bootloader until SIGTERM or HOLD_LIMIT seconds pass.
 *   --check  print the sha256 of the source it was built from and touch nothing: the installer's test that the build
 *            runs, and whether the installed one is current.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#ifndef DEVICE /* the paths are set at build time only by its tests */
#define DEVICE "/dev/ttyS4"
#define CATCH "/run/vo-thr-catch.json"
#define PID "/run/vo-thr-catch.pid"
#endif
#define HOLD_INTERVAL 2.0
#define HOLD_LIMIT 60.0
#ifndef SOURCE_SHA256
#define SOURCE_SHA256 "unknown"
#endif
#define SECONDS 1.5
#define WAIT 0.2

/* Frame kind 0, payload 01 00, CRC16: the THR tool's identity request. */
static const unsigned char IDENTITY[] = {0xa5, 0x00, 0x00, 0x02, 0x01, 0x00, 0xaf, 0x7b};

static volatile sig_atomic_t stop;

static void on_term(int signum) {
    (void)signum;
    stop = 1;
}

static double clock_of(clockid_t id) {
    struct timespec now;
    clock_gettime(id, &now);
    return now.tv_sec + now.tv_nsec / 1e9;
}

static double elapsed(void) { return clock_of(CLOCK_BOOTTIME); }
static double monotonic(void) { return clock_of(CLOCK_MONOTONIC); }

static unsigned crc16(const unsigned char *data, size_t size) {
    unsigned value = 0xFFFF;
    for (size_t i = 0; i < size; i++) {
        value ^= data[i];
        for (int bit = 0; bit < 8; bit++)
            value = (value >> 1) ^ ((value & 1) ? 0xA001 : 0);
    }
    return value ^ 0xFFFF;
}

static unsigned char buffer[512];
static size_t buffered;
static unsigned char reply[100];
static int reply_size = -1;
static unsigned char received[512]; /* everything that came back, for the record */
static size_t received_size;

static void drop(size_t count) {
    memmove(buffer, buffer + count, buffered - count);
    buffered -= count;
}

/* Consume complete frames at the front of buffer; 1 once a CRC-valid frame of kind 0 is read into reply. */
static int frames(void) {
    while (buffered) {
        if (buffer[0] != 0xA5) {
            drop(1);
            continue;
        }
        if (buffered < 4)
            return 0;
        size_t size = (size_t)buffer[2] << 8 | buffer[3];
        if (size > 100) {
            drop(1);
            continue;
        }
        if (buffered < size + 6)
            return 0;
        int valid = ((unsigned)buffer[size + 4] << 8 | buffer[size + 5]) == crc16(buffer + 1, size + 3);
        int kind = buffer[1];
        if (valid && kind == 0) {
            memcpy(reply, buffer + 4, size);
            reply_size = (int)size;
        }
        drop(size + 6);
        if (reply_size >= 0)
            return 1;
    }
    return 0;
}

static int raw_115200(int fd) {
    struct termios attributes;
    if (tcgetattr(fd, &attributes))
        return -1;
    attributes.c_iflag = 0;
    attributes.c_oflag = 0;
    attributes.c_cflag = CS8 | CREAD | CLOCAL;
    attributes.c_lflag = 0;
    attributes.c_cc[VMIN] = 0;
    attributes.c_cc[VTIME] = 0;
    if (cfsetispeed(&attributes, B115200) || cfsetospeed(&attributes, B115200) ||
        tcsetattr(fd, TCSANOW, &attributes))
        return -1;
    return tcflush(fd, TCIOFLUSH);
}

/* Sleep up to seconds, or until fd is readable when fd >= 0; returns select's result. SIGTERM interrupts it. */
static int wait_for(int fd, double seconds) {
    fd_set readable;
    FD_ZERO(&readable);
    if (fd >= 0)
        FD_SET(fd, &readable);
    if (seconds < 0)
        seconds = 0;
    struct timeval timeout = {(time_t)seconds, (suseconds_t)((seconds - (time_t)seconds) * 1e6)};
    return select(fd >= 0 ? fd + 1 : 0, fd >= 0 ? &readable : NULL, NULL, NULL, &timeout);
}

static void hex(FILE *out, const unsigned char *data, size_t size) {
    for (size_t i = 0; i < size; i++)
        fprintf(out, "%02x", data[i]);
}

/* The record vo_thr_catch.py writes, in CATCH; 1 once it is in place. */
static int write_record(double started, double first_sent, double last_sent, double reply_at, int held,
                        const char *failed, int error) {
    FILE *out = fopen(CATCH ".tmp", "w");
    if (!out)
        return 0;
    fprintf(out, "{\"started\": %.9f", started);
    if (first_sent >= 0)
        fprintf(out, ", \"first_sent\": %.9f", first_sent);
    if (last_sent >= 0)
        fprintf(out, ", \"last_sent\": %.9f", last_sent);
    if (reply_size >= 0) {
        fputs(", \"reply\": \"", out);
        hex(out, reply, (size_t)reply_size);
        fprintf(out, "\", \"reply_at\": %.9f", reply_at);
    }
    if (held)
        fputs(", \"held\": true", out);
    if (received_size) {
        fputs(", \"received\": \"", out);
        hex(out, received, received_size);
        fputs("\"", out);
    }
    if (failed)
        fprintf(out, ", \"error\": \"%s: %s\"", failed, strerror(error));
    fputs(", \"catcher\": \"compiled\"}\n", out);
    return !fclose(out) && !chmod(CATCH ".tmp", 0644) && !rename(CATCH ".tmp", CATCH);
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--check"))
        return puts(SOURCE_SHA256) < 0;
    if (argc != 2 || strcmp(argv[1], "--early"))
        return 2;

    struct sigaction action;
    memset(&action, 0, sizeof action);
    action.sa_handler = on_term; /* no SA_RESTART: SIGTERM ends the hold's wait at once */
    sigaction(SIGTERM, &action, NULL);
    FILE *pid = fopen(PID, "w");
    if (pid) {
        fprintf(pid, "%d\n", (int)getpid());
        fclose(pid);
    }

    double started = elapsed(), first_sent = -1, last_sent = -1, reply_at = -1;
    int held = 0;
    const char *failed = NULL;
    int error = 0;
    struct group *dialout = getgrnam("dialout");
    int fd = -1;
    if (!dialout)
        failed = "no dialout group", error = ENOENT;
    else if (chown(DEVICE, (uid_t)-1, dialout->gr_gid) || chmod(DEVICE, 0660))
        failed = DEVICE, error = errno;
    else if ((fd = open(DEVICE, O_RDWR | O_NOCTTY | O_NONBLOCK)) < 0)
        failed = DEVICE, error = errno;
    else if (raw_115200(fd))
        failed = DEVICE, error = errno;
    if (!failed) {
        double end = monotonic() + SECONDS;
        while (monotonic() < end && reply_size < 0 && !stop) {
            if (write(fd, IDENTITY, sizeof IDENTITY) < 0) {
                failed = DEVICE, error = errno;
                break;
            }
            last_sent = elapsed();
            if (first_sent < 0)
                first_sent = last_sent;
            double wait = monotonic() + WAIT;
            while (monotonic() < wait && reply_size < 0 && !stop) {
                if (wait_for(fd, wait - monotonic()) <= 0)
                    continue;
                ssize_t count = read(fd, buffer + buffered, sizeof buffer - buffered);
                if (count <= 0)
                    continue;
                size_t room = sizeof received - received_size;
                memcpy(received + received_size, buffer + buffered, (size_t)count < room ? (size_t)count : room);
                received_size += (size_t)count < room ? (size_t)count : room;
                buffered += (size_t)count;
                if (frames())
                    reply_at = elapsed();
            }
        }
        /* Each identity request holds an answering bootloader for a session's time; it answers no more of them. */
        double limit = monotonic() + HOLD_LIMIT;
        while (!failed && reply_size >= 0 && !stop && monotonic() < limit) {
            wait_for(-1, HOLD_INTERVAL);
            if (stop)
                break;
            if (write(fd, IDENTITY, sizeof IDENTITY) < 0) {
                failed = DEVICE, error = errno;
                break;
            }
            last_sent = elapsed();
            held = 1;
        }
    }
    if (fd >= 0)
        close(fd);

    int written = write_record(started, first_sent, last_sent, reply_at, held, failed, error);
    unlink(PID);
    return written ? 0 : 1;
}
