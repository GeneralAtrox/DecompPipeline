/* The 68000 core (port/saturn/sat_m68k*.c) on hand-assembled programs: a flat 64 KB bus, run to STOP. Built and run by
 * tests/test_sat_m68k.py with the kit at /W4 /WX. */
#include <stdio.h>
#include <string.h>
#include "sat_m68k_i.h"
static u8 ram[0x10000];
u32 sat_snd_rd(u32 a, int sz){ u32 v=0; a&=0xffff; for(int i=0;i<sz;i++) v=v<<8|ram[(a+i)&0xffff]; return v; }
void sat_snd_wr(u32 a, int sz, u32 v){ a&=0xffff; for(int i=0;i<sz;i++) ram[(a+i)&0xffff]=(u8)(v>>(8*(sz-1-i))); }
static int fails;
static void load(const unsigned short *w, int n){ memset(ram,0,sizeof ram); ram[0]=0;ram[1]=0;ram[2]=0x80;ram[3]=0; ram[4]=0;ram[5]=0;ram[6]=1;ram[7]=0; for(int i=0;i<n;i++){ram[0x100+2*i]=(u8)(w[i]>>8);ram[0x101+2*i]=(u8)w[i];} m68k_reset(); m68k_run(100000);}
#define CHECK(name,cond) do{ if(!(cond)){printf("FAIL %s: d0=%08x d1=%08x d2=%08x a0=%08x sr=%04x\n",name,m68k.d[0],m68k.d[1],m68k.d[2],m68k.a[0],m68k.sr);fails++;} else printf("ok %s\n",name);}while(0)
int main(void){
  { unsigned short p[]={0x7005,0x5640,0x0C40,0x0008,0x6702,0x7063,0x7201,0xD240,0xE549,0x4E72,0x2700};
    load(p,11); CHECK("alu",m68k.d[0]==8 && m68k.d[1]==0x24); }
  { unsigned short p[]={0x203C,0x0001,0x0000,0x80FC,0x0003,0x4840,0x4E72,0x2700};
    load(p,8); CHECK("divu",m68k.d[0]==0x55550001u); }
  { unsigned short p[]={0x7000,0x7204,0xD041,0x51C9,0xFFFC,0x4E72,0x2700};
    load(p,7); CHECK("dbra",m68k.d[0]==10 && (m68k.d[1]&0xffff)==0xffff); }
  { unsigned short p[]={0x4FF9,0x0000,0x8000,0x2F3C,0x1234,0x5678,0x221F,0x6100,0x0004,0x4E72,0x2700,0x7407,0x4E75};
    load(p,13); CHECK("stack-bsr",m68k.d[1]==0x12345678u && m68k.d[2]==7); }
  { unsigned short p[]={0x203C,0x8000,0x0001,0xE280,0x40C2,0x4E72,0x2700}; /* asr.l #1,d0; move sr,d2 */
    load(p,7); CHECK("asr",m68k.d[0]==0xC0000000u && (m68k.d[2]&FC)); }
  { unsigned short p[]={0x7001,0x7200,0x4881,0x4E72,0x2700}; load(p,5); CHECK("ext",m68k.d[1]==0); }
  { unsigned short p[]={0x203C,0x0000,0x00FF,0x4880,0x4E72,0x2700}; load(p,6); CHECK("ext.w",(m68k.d[0]&0xffff)==0xFFFF); }
  { unsigned short p[]={0x41F9,0x0000,0x0200,0x20BC,0xAABB,0xCCDD,0x2010,0x4E72,0x2700}; /* lea 0x200,a0; move.l #..,(a0); move.l (a0),d0 */
    load(p,9); CHECK("mem",m68k.d[0]==0xAABBCCDDu && m68k.a[0]==0x200); }
  { unsigned short p[]={0x7005,0x0800,0x0000,0x4E72,0x2700}; /* btst #0,d0 */ load(p,5); CHECK("btst",!(m68k.sr&FZ)); }
  { unsigned short p[]={0x4FF9,0x0000,0x8000,0x48E7,0xC0C0,0x4CDF,0x0303,0x4E72,0x2700}; /* movem push/pop */
    load(p,9); CHECK("movem",m68k.a[7]==0x8000); }
  printf("%s\n", fails?"FAILED":"all ok"); return fails; }
