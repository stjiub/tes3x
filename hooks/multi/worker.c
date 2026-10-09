static void file_work(void)
{
    trust_write();
    bulk_work();
    up_work();
}

/* The KTHREAD running now: the KPCR's PrcbData.CurrentThread. */
static void *current_thread(void)
{
    void *t;

    __asm__ volatile("movl %%fs:0x28, %0" : "=r"(t));
    return t;
}

/* Above the game thread, which never yields: at its priority the worker would wait for the end
 * of its time slice, and the bulk window with it. It mostly waits on the disk. */
#define WORKER_PRIORITY 2

static void __stdcall worker_thread(void *context)
{
    long long wait = -(long long)WORKER_SLEEP_MS * 10000;

    (void)context;
    KeSetBasePriorityThread(current_thread(), WORKER_PRIORITY);
    while (!worker.stop) {
        KeDelayExecutionThread(0, 0, &wait);
        file_work();
    }
    trust_write();
    worker.running = 0;
}

static void __stdcall worker_system(void(__stdcall *start)(void *), void *context)
{
    start(context);
    PsTerminateSystemThread(0);
}

static void worker_start(void)
{
    void *h = 0;
    u32 status;

    if (worker.running)
        return;
    worker.stop = 0;
    worker.running = 1;
    status = PsCreateSystemThreadEx(&h, 0, 0x4000, 0, 0, worker_thread, 0, 0, 0,
                                    (void *)worker_system);
    if (status) {
        worker.running = 0;
        tes3x_log_hex("net.worker_failed", status);
    } else {
        NtClose(h);
        tes3x_log_hex3("net.worker_priority", (u32)KeQueryBasePriorityThread(current_thread()),
                       WORKER_PRIORITY, 0);
    }
}

/* Waits up to two seconds for the file work in hand. */
static void worker_stop(void)
{
    long long wait = -(long long)WORKER_SLEEP_MS * 10000;
    u32 i;

    worker.stop = 1;
    for (i = 0; i < 2000 / WORKER_SLEEP_MS && worker.running; i++)
        KeDelayExecutionThread(0, 0, &wait);
}

/* Game thread, each frame while up. Without a worker the file work runs here. */
static void file_frame(void)
{
    u32 flags, i, n;
    const char *tags[WORKER_LOGS];
    u32 values[WORKER_LOGS][3];

    flags = lock();
    n = worker.count;
    for (i = 0; i < n; i++) {
        tags[i] = worker.log[i].tag;
        values[i][0] = worker.log[i].a;
        values[i][1] = worker.log[i].b;
        values[i][2] = worker.log[i].c;
    }
    worker.count = 0;
    unlock(flags);
    for (i = 0; i < n; i++)
        tes3x_log_hex3(tags[i], values[i][0], values[i][1], values[i][2]);
    /* Not before the server's key is known: the section always carries one. */
    if (trust.dirty && trust.has_server && !trust_job.pending)
        trust_save();
    if (!worker.running)
        file_work();
    up_frame();
}
