.PHONY: release san clean
SOURCES = $(wildcard *.c)
release:
	mkdir -p build/release
	gcc -std=c99 -Wall -Wextra -Werror $(SOURCES) -o build/release/protocol-server
san:
	mkdir -p build/san
	gcc -std=c99 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-pie -no-pie $(SOURCES) -o build/san/protocol-server
clean:
	rm -rf build
