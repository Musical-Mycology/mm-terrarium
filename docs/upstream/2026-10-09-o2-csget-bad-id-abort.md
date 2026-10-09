# DRAFT for the weekly update to Roger (not sent)

## O2: a cs/get with an unknown o2lite bridge id aborts the host

`src/bridge.cpp:172` (rbdannenberg/o2 main bb394bf, unchanged since cf8e303):

```cpp
o2_drop_message("bad ID in o2lite/cs/get message", msgdata);
```

`o2_drop_message` is `(const char *warn, bool free_the_msg)`. `msgdata` is a
non-null pointer, so it converts to `true` and `o2_drop_message` calls
`o2_complete_delivery()`, popping and freeing the current message. The
handler returns into `o2_msg_deliver`, which calls `o2_complete_delivery()`
again at its end (msgsend.cpp, STEP 7). That pops a message that is no longer
there: with an empty `o2_ctx->msgs` it trips `assert(o2_ctx->msgs)` in
`o2_postpone_delivery` and the process aborts (with NDEBUG it dereferences
NULL; with a nested delivery it frees the caller's message). The other
branches of `o2_bridge_csget_handler` use `o2_drop_msg_data(..., msgdata)`,
which does not free.

The branch runs whenever `/_o2/o2lite/cs/get` carries an id that
`o2lite_protocol->find()` does not hold. o2lite sends cs/get by UDP, but the
bridge lives and dies with the TCP connection, so the host aborts whenever a
client's TCP closes while one of its cs/get datagrams is still unread. A
single UDP datagram with any unknown id does the same.

Reproduction (Arco server linked against o2 f21499e, macOS 26.6): connect
one o2litepy client and wait for its id and clock sync. Call `tcp_close()`
and wait 1 s. Then set `bridge_id` back to the old id and call
`_clock_ping()`. Arco aborts every time:

```
__assert_rtn
o2_postpone_delivery
o2_complete_delivery
o2_msg_deliver(O2node*, Services_entry*)
o2_send_local(O2node*, Services_entry*)
o2_service_msg_send(O2node*, Services_entry*)
o2_message_send
Proxy_info::deliver(O2netmsg*)
Fds_info::read_event_handler()
o2n_recv()
o2_poll
main
```

We hit it with 30 simulated o2lite devices. Arco fell several seconds behind.
One device's link timer closed its own TCP connection, and a cs/get it had
already sent was read after the close. ESP32 devices that drop Wi-Fi follow
the same path.

Fix: `o2_drop_msg_data("bad ID in o2lite/cs/get message", msgdata);`

## O2: a host reads one message per socket per o2_poll

`o2n_recv` (`src/o2network.cpp:1093` at bb394bf) calls
`read_event_handler()` once for each readable socket, and the handler reads
one UDP datagram or one whole TCP message, then returns. A socket with a
backlog yields one message per `o2_poll`. The Arco server calls `o2_poll`
once per main-loop pass at `polling_rate` (default 500 Hz,
`server/src/arco.cpp:1333` and `:1389`), so each socket delivers at most
about 500 messages per second.

All o2lite clients' UDP traffic arrives on the host's single UDP server
socket. With Control sending LED frames by UDP to N devices at 44 Hz, Arco
falls behind at 6 devices: frame lateness at 6, 10 and 30 devices was
1.4 s, 4.9 s and 5.5 s median, and grew for the whole run. CPU stayed under
22% of one core. The number of frames delivered per run was the same at
every device count (about 4.4k), and it rose in proportion when we raised
`polling_rate` (16.5k at 2000 Hz). At 5000 Hz, 30 devices run at 2.4 ms
median lateness. The macOS UDP receive buffer (786 KB) holds the backlog,
so overload shows as seconds of delay rather than drops. That backlog is
also what left the stale cs/get above unread long enough to hit the abort.

We now run Arco at `polling_rate` 5000.
