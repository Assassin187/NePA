#include <signal.h>
#include <unistd.h>

static volatile sig_atomic_t nepa_running = 1;
static void nepa_stop(int signal_number) { (void)signal_number; nepa_running = 0; }
int main(void) {
    (void)signal(SIGTERM, nepa_stop);
    while (nepa_running) { (void)sleep(1); }
    return 0;
}
