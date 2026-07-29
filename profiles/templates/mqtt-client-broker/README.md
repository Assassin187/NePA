# Generated MQTT 3.1.1 C workspace

Build with `make` or `make SAN=1`. The broker and client use a single-threaded POSIX event loop.
Protocol state lives in the session/broker core; the net layer only owns connection lifecycle
and routes bounded output items by stable connection id.
