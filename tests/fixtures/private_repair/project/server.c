#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;
static void stop_server(int sig) { (void)sig; stopping = 1; }

static void echo_connection(int client)
{
    unsigned char buffer[4096];
    struct pollfd ready = {client, POLLIN, 0};
    while (!stopping) {
        int result = poll(&ready, 1, 100);
        if (result < 0) { if (errno == EINTR) continue; break; }
        if (result == 0) continue;
        ssize_t count = recv(client, buffer, sizeof buffer, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        size_t remaining = (size_t)count - 1;
        size_t offset = 0;
        while (remaining > 0 && !stopping) {
            ssize_t sent = send(client, buffer + offset, remaining, 0);
            if (sent < 0 && errno == EINTR) continue;
            if (sent <= 0) return;
            offset += (size_t)sent;
            remaining -= (size_t)sent;
        }
    }
}

int main(int argc, char **argv)
{
    const char *host = "127.0.0.1";
    int port = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--host") == 0) host = argv[i + 1];
        else if (strcmp(argv[i], "--port") == 0) port = atoi(argv[i + 1]);
        else return 2;
    }
    if (port < 1 || port > 65535) return 2;
    struct sigaction action;
    memset(&action, 0, sizeof action);
    action.sa_handler = stop_server;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) != 0 || sigaction(SIGINT, &action, NULL) != 0) return 2;
    signal(SIGPIPE, SIG_IGN);
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) return 2;
    int one = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in address;
    memset(&address, 0, sizeof address);
    address.sin_family = AF_INET;
    address.sin_port = htons((unsigned short)port);
    if (inet_pton(AF_INET, host, &address.sin_addr) != 1 ||
        bind(listener, (struct sockaddr *)&address, sizeof address) != 0 || listen(listener, 8) != 0) {
        close(listener);
        return 2;
    }
    struct pollfd ready = {listener, POLLIN, 0};
    while (!stopping) {
        int result = poll(&ready, 1, 100);
        if (result < 0) { if (errno == EINTR) continue; close(listener); return 2; }
        if (result == 0) continue;
        int client = accept(listener, NULL, NULL);
        if (client < 0) { if (errno == EINTR) continue; close(listener); return 2; }
        echo_connection(client);
        close(client);
    }
    close(listener);
    return 0;
}
