XBE_TITLE = TES3X\ manager
SRCS = $(CURDIR)/main.c $(CURDIR)/builds.c $(CURDIR)/xbe.c $(CURDIR)/json.c \
       $(CURDIR)/sha256.c $(CURDIR)/agent.c $(CURDIR)/zstd/zstddeclib.c \
       $(CURDIR)/../hooks/monocypher.c $(CURDIR)/../hooks/tes3xnoise.c
NXDK_DIR ?= $(CURDIR)/../../..
NXDK_SDL = y
NXDK_NET = y

include $(NXDK_DIR)/Makefile
