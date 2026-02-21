BINARY  := yeehaw
GOFLAGS := GOTOOLCHAIN=local

.PHONY: all build test vet check clean install

all: check build

build:
	$(GOFLAGS) go build -o $(BINARY) ./cmd/yeehaw

test:
	$(GOFLAGS) go test ./...

vet:
	$(GOFLAGS) go vet ./...

check: vet test

clean:
	rm -f $(BINARY)

install: build
	install -m 755 $(BINARY) /usr/local/bin/
