# Stable TES3X dashboard-agent launcher. Updates replace agent_body.py, then ask it to reload;
# this file stays open until the dashboard ends and therefore must not change between releases.
import os
import time

BODY = os.path.join(os.path.dirname(__file__), "agent_body.py")


def log(text):
    print("tes3xagent: %s" % text)


while True:
    namespace = {"__file__": BODY, "__name__": "tes3xagent_body"}
    try:
        execfile(BODY, namespace)
        action = namespace["run"]()
    except Exception as error:
        log("body failed: %s" % error)
        time.sleep(10)
        continue
    if action != "reload":
        break
    time.sleep(2)
