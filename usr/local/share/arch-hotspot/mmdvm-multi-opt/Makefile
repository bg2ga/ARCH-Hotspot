CXX      = c++
CXXFLAGS += -O3 $(if $(DEBUG),-g,) -Wall -Wextra -Wpedantic -std=gnu++17 -fpermissive -MMD -MD -pthread
CXXFLAGS += -ffunction-sections -fdata-sections
LDFLAGS  += -Wl,--gc-sections -L/usr/local/lib
LIBS     += -lpthread -lmosquitto -lSoapySDR -lliquid
ifeq ($(shell uname -s),Darwin)
	CXXFLAGS+= -I/opt/homebrew/include -Wno-gnu-zero-variadic-macro-arguments
	LDFLAGS+= -L/opt/homebrew/lib
endif
CXXSRCS = $(wildcard *.cpp)
CXXDEPS = $(CXXSRCS:.cpp=.d)
CXXOBJS = $(CXXSRCS:.cpp=.o)
all: MMDVM-Multi
MMDVM-Multi: $(CXXOBJS)
	$(CXX) $(CXXOBJS) $(LDFLAGS) $(LIBS) -o $@
%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<
-include $(CXXDEPS)
MMDVM-Multi.o: GitVersion.h FORCE
.PHONY: GitVersion.h FORCE install clean strip-release
FORCE:
install: MMDVM-Multi
	install -m 755 MMDVM-Multi /usr/local/bin/
	install -m 644 MMDVM-Multi.ini /etc
strip-release: MMDVM-Multi
	strip --strip-unneeded MMDVM-Multi
clean:
	$(RM) MMDVM-Multi *.o *.d *.bak *~ GitVersion.h
GitVersion.h:
ifneq ("$(wildcard .git/index)","")
	echo "const char *gitversion = \"$(shell git rev-parse HEAD)\";" > $@
else
	echo "const char *gitversion = \"0000000000000000000000000000000000000000\";" > $@
endif
