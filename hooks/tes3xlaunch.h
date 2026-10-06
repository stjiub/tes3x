#ifndef TES3X_LAUNCH_H
#define TES3X_LAUNCH_H

#include "tes3xlog.h"

/* Start the XBE at `path` (C:, E:, F:, G: or a \Device\ path) in its own folder, as dashboards
 * do, since XLaunchNewImage takes only D:\ paths. Returns only when the path cannot be used. */
void tes3x_launch(const char *path);

#endif
