XBE_TITLE = TES3X\ manager
# mbedTLS, for the update feed over HTTPS; tes3x_nxdk.py fetches it beside the build
MBEDTLS = $(CURDIR)/../mbedtls
SRCS = $(CURDIR)/main.c $(CURDIR)/builds.c $(CURDIR)/xbe.c $(CURDIR)/json.c \
       $(CURDIR)/sha256.c $(CURDIR)/agent.c $(CURDIR)/console.c $(CURDIR)/update.c \
       $(CURDIR)/http.c $(CURDIR)/servers.c $(CURDIR)/install.c $(CURDIR)/monocypher-ed25519.c $(CURDIR)/zstd/zstddeclib.c        $(CURDIR)/gfx.c $(CURDIR)/ui.c $(CURDIR)/screens.c $(CURDIR)/fontdata.c \
       $(CURDIR)/../hooks/monocypher.c $(CURDIR)/../hooks/tes3xnoise.c \
       $(wildcard $(MBEDTLS)/library/*.c)
NXDK_DIR ?= $(CURDIR)/../../..
NXDK_SDL = y
NXDK_NET = y
# nxdk sets no optimisation level, and the crypto and hashing are several times slower without one
override CFLAGS += -O2
override CFLAGS += -I$(CURDIR)/../hooks -I$(CURDIR) -I$(CURDIR)/compat -I$(MBEDTLS)/include \
                   -DMBEDTLS_CONFIG_FILE=\"tls_config.h\"
# x509_crt.c asks the linker for Winsock, which compat/ stands in for
override LDFLAGS += -nodefaultlib:ws2_32.lib
# Updates are checked against the project's release key; a fork puts its own in keys/release.pub.
# Test builds pass -DRELEASE_PUB with a key of their own.
ifeq ($(findstring RELEASE_PUB,$(CFLAGS)),)
RELEASE_PUB := $(firstword $(shell cat $(CURDIR)/../keys/release.pub))
override CFLAGS += -DRELEASE_PUB=\"$(RELEASE_PUB)\"
endif

include $(NXDK_DIR)/Makefile
