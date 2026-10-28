/* Platform layer: the network library with no modem behind it.
 *
 * A game's network play runs over the Dreamcast's modem stack (a TCP/IP stack, PPP and a packet driver; the names are
 * the game's registry names for library functions with no SDK name). The port has no modem: every request and
 * start-up call fails (-1), so the game takes its no-network path; the void calls do nothing and the one getter
 * reads 0. None of them logs: they are reached only from the network screens.
 *
 * Lib_RunServers is the stack's server pump, which a game calls every tick: it runs the stack's servers once the
 * stack reports its link up, which with no modem it never does, so it does nothing.
 *
 * Needs from the game's headers: the function names below as the game's registry has them. */
#include "game.h"

#define NET_NONE (-1)

void Net_Trace(void) {}
void Net_ReqCreateEndpoint(void *req) { (void)req; }
s32 Net_ReqSetConfig(void *req) { (void)req; return NET_NONE; }
void Net_ReqResetEndpoint(void *req) { (void)req; }
s32 Net_ReqTcpConnect(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqTcpListen(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqTcpAccept(void *req, u32 *ipAddr, u16 *port, u16 *localPort)
{
    (void)req;
    (void)ipAddr;
    (void)port;
    (void)localPort;
    return NET_NONE;
}
void Net_ReqDeleteEndpoint(void *req) { (void)req; }
s32 Net_ReqTcpShutdown(void *req) { (void)req; return NET_NONE; }
void Net_ReqTcpAbort(void *req) { (void)req; }
s32 Net_ReqGetAddress(void *req, u32 *ipAddr, u16 *port, u16 *localPort)
{
    (void)req;
    (void)ipAddr;
    (void)port;
    (void)localPort;
    return NET_NONE;
}
s32 Net_ReqGetStatus(void *req, u16 *state, u16 *value1, u16 *value2, u16 *value3)
{
    (void)req;
    (void)state;
    (void)value1;
    (void)value2;
    (void)value3;
    return NET_NONE;
}
s32 Net_ReqTcpSendGather(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqTcpSend(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqTcpRecv(void *req, u16 *length) { (void)req; (void)length; return NET_NONE; }
u32 Net_GetLibraryValue(void) { return 0u; }
void Net_ReqSetValue(void *req) { (void)req; }
s32 Net_ReqSetOption(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqGetOption(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqUdpCreate(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqUdpSendGather(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqCreateRaw(void *req) { (void)req; return NET_NONE; }
s32 Net_ReqRawSendGather(void *req) { (void)req; return NET_NONE; }
void Net_ReqSetIpAddress(void *req) { (void)req; }
void Net_ReqSetRoute(void *req) { (void)req; }
s32 Net_ReqAddressCmdA(void *req) { (void)req; return NET_NONE; }
void Net_ReqArpAdd(void *req) { (void)req; }
void Net_ReqArpDelete(void *req) { (void)req; }
s32 Net_ReqAddressCmdB(void *req) { (void)req; return NET_NONE; }
s32 Net_Poll(void) { return NET_NONE; }
void Net_OsWaitA(void) {}
void Net_OsWaitB(void) {}
void Net_OsEnterCritical(void) {}
void Net_OsLeaveCritical(void) {}
s32 Net_Init(void) { return NET_NONE; }
s32 Net_PacketDriverInit(u32 unusedR4, s32 value1, s32 value2, s32 value3, s32 value4, s32 value5)
{
    (void)unusedR4;
    (void)value1;
    (void)value2;
    (void)value3;
    (void)value4;
    (void)value5;
    return NET_NONE;
}
s32 Net_PacketDriverProcess(void *packet) { (void)packet; return NET_NONE; }
s32 Net_PacketDriverCmdA(u32 unusedR4, u32 unusedR5) { (void)unusedR4; (void)unusedR5; return NET_NONE; }
s32 Net_PacketDriverCmdB(u32 unusedR4, u32 unusedR5) { (void)unusedR4; (void)unusedR5; return NET_NONE; }
s32 Net_PppSetConfig(s32 value1, u32 value2, u32 value3, u32 value4, u32 value5, u32 value6, u32 value7)
{
    (void)value1;
    (void)value2;
    (void)value3;
    (void)value4;
    (void)value5;
    (void)value6;
    (void)value7;
    return NET_NONE;
}
s32 Net_PppShutdownA(void) { return NET_NONE; }
s32 Net_PppShutdownB(void) { return NET_NONE; }
void Net_Start(void) {}
void Net_Stop(void) {}
void Net_EnterCritical(void) {}
void Net_LeaveCritical(void) {}
void Net_CallHook(void) {}
void Net_Delay(s32 count) { (void)count; }
void Net_Wait(s32 ticks) { (void)ticks; }
void Net_ClearPending(void) {}
s32 Net_GetStatusFlag(void) { return NET_NONE; }
void Net_ResetCounters(void) {}
s32 Net_PppConnect(void *config, s32 mode) { (void)config; (void)mode; return NET_NONE; }
void Net_UpdateAddressInfo(void) {}
void Net_SockCmdA(void) {}
s32 Net_SockCmdB(u32 value1, u32 value2) { (void)value1; (void)value2; return NET_NONE; }
void Net_SockCmdC(void) {}
void Net_SockCmdD(u32 value) { (void)value; }
void Net_SockCmdE(u32 value) { (void)value; }
void Net_DelayTicks(s32 count) { (void)count; }
s32 Net_Lock(void) { return NET_NONE; }
s32 Net_Unlock(void) { return NET_NONE; }
s32 Net_SockGetStatus(s32 sock, u16 *state, u16 *value1, u16 *value2, u16 *value3)
{
    (void)sock;
    (void)state;
    (void)value1;
    (void)value2;
    (void)value3;
    return NET_NONE;
}
void Net_SockOpenA(s32 sock, u32 ipAddr, s32 port) { (void)sock; (void)ipAddr; (void)port; }
void Net_SockOpenB(s32 sock, u32 ipAddr, s32 port) { (void)sock; (void)ipAddr; (void)port; }
void Net_SockOpenC(s32 sock, u32 ipAddr, s32 port) { (void)sock; (void)ipAddr; (void)port; }
s32 Net_TcpSendWait(s32 sock, const void *buf, s32 length) { (void)sock; (void)buf; (void)length; return NET_NONE; }
s32 Net_TcpSend(s32 sock, const void *buf, s32 length) { (void)sock; (void)buf; (void)length; return NET_NONE; }
s32 Net_TcpRecv(s32 sock, u8 *buf, s32 length) { (void)sock; (void)buf; (void)length; return NET_NONE; }
s32 Net_SockRecv(s32 sock, u8 *buf, s32 length) { (void)sock; (void)buf; (void)length; return NET_NONE; }
s32 Net_SockWaitEstablished(s32 sock) { (void)sock; return NET_NONE; }
void Net_TcpClose(s32 sock) { (void)sock; }
s32 Net_SockGetAddress(s32 sock, u16 *port, u32 *ipAddr, u16 *localPort)
{
    (void)sock;
    (void)port;
    (void)ipAddr;
    (void)localPort;
    return NET_NONE;
}
void Net_UdpOpen(s32 sock, u32 ipAddr, s32 port) { (void)sock; (void)ipAddr; (void)port; }
void Net_UdpSendTo(s32 sock, const void *buf, s32 length, u32 ipAddr, s32 port)
{
    (void)sock;
    (void)buf;
    (void)length;
    (void)ipAddr;
    (void)port;
}
void Net_UdpClose(s32 sock) { (void)sock; }

void Lib_RunServers(void) {}
