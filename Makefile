# Convenience: forward all targets to the software/ Makefile.
#   make up   ->   make -C software up
MAKEFLAGS += --no-print-directory

.DEFAULT_GOAL := help

help:
	@$(MAKE) -C software/v0 help

# Catch-all: forward any other target (up, serve, capture, ...) into software/v0/.
%:
	@$(MAKE) -C software/v0 $@
