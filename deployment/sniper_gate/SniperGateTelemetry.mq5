// DK Capital Sniper Pro read-only telemetry publisher.
// Attach to an ADDITIONAL XAUUSD chart in the SAME terminal/account as DiscordLimitUnified.
// This EA cannot submit, modify or cancel orders. It does not alter DiscordLimitUnified.
// Publishes closed M1 candles + closing deal reasons for the external Python gate.
#property strict
#property version "1.00"

input int PublishSeconds = 5;
input int BackfillM1Bars = 120;
input int DealLookbackHours = 4;
input ulong SniperMagicNumber = 26110430;
input string M1File = "discord_limit_xau_m1.csv";
input string EnrichedDealFile = "discord_limit_deals_enriched.csv";
input string IdentityFile = "discord_limit_gate_telemetry_identity.csv";

bool FileSwap(string temporary,string destination) {
   ResetLastError();
   if(!FileMove(temporary,FILE_COMMON,destination,FILE_COMMON|FILE_REWRITE)) {
      Print("SniperGateTelemetry FileMove failed ",destination," error=",GetLastError());
      return false;
   }
   return true;
}

int OnInit() {
   if(StringFind(_Symbol,"XAUUSD")!=0) {
      Print("SniperGateTelemetry: attach to XAUUSD chart only");
      return INIT_FAILED;
   }
   if(PublishSeconds<1 || PublishSeconds>60 ||
      BackfillM1Bars<40 || BackfillM1Bars>1000 ||
      DealLookbackHours<3 || DealLookbackHours>24) {
      return INIT_PARAMETERS_INCORRECT;
   }
   if(!EventSetTimer(PublishSeconds)) {
      Print("SniperGateTelemetry cannot set timer: ",GetLastError());
      return INIT_FAILED;
   }
   Print("SniperGateTelemetry started on ",_Symbol,
         "; read-only, no order functions; account=",AccountInfoInteger(ACCOUNT_LOGIN));
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) {
   EventKillTimer();
}
void OnTick() {
   // Intentionally empty: no order placement or management.
}
string SideLabel(long t) {
   if(t==(long)DEAL_TYPE_BUY) return "BUY";
   if(t==(long)DEAL_TYPE_SELL) return "SELL";
   return "OTHER";
}
string EntryLabel(long e) {
   if(e==(long)DEAL_ENTRY_IN) return "IN";
   if(e==(long)DEAL_ENTRY_OUT) return "OUT";
   if(e==(long)DEAL_ENTRY_INOUT) return "INOUT";
   if(e==(long)DEAL_ENTRY_OUT_BY) return "OUT_BY";
   return "OTHER";
}
string ReasonLabel(long r) {
   if(r==(long)DEAL_REASON_SL) return "SL";
   if(r==(long)DEAL_REASON_TP) return "TP";
   if(r==(long)DEAL_REASON_SO) return "SO";
   return "OTHER";
}
void PublishBars() {
   // This uses broker server clock text, interpreted using the gate's
   // independently VERIFIED server UTC offset. Do not guess the offset.
   MqlRates bars[];
   ArraySetAsSeries(bars,false);
   int count=CopyRates(_Symbol,PERIOD_M1,1,BackfillM1Bars,bars);
   if(count<40) {
      Print("SniperGateTelemetry: insufficient closed M1 bars ",count);
      return;
   }
   string tmp=M1File+".tmp";
   int h=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(h==INVALID_HANDLE) {
      Print("SniperGateTelemetry cannot write bars ",GetLastError());
      return;
   }
   FileWrite(h,"time","open","high","low","close","volume");
   for(int i=0;i<count;i++) {
      FileWrite(h,TimeToString(bars[i].time,TIME_DATE|TIME_SECONDS),
                 DoubleToString(bars[i].open,_Digits),
                 DoubleToString(bars[i].high,_Digits),
                 DoubleToString(bars[i].low,_Digits),
                 DoubleToString(bars[i].close,_Digits),
                 (string)bars[i].tick_volume);
   }
   FileClose(h);
   FileSwap(tmp,M1File);
}
void PublishDeals() {
   datetime end_time=TimeCurrent();
   datetime start_time=end_time-(datetime)(DealLookbackHours*3600);
   if(!HistorySelect(start_time,end_time)) {
      Print("SniperGateTelemetry HistorySelect failed ",GetLastError());
      return;
   }
   string tmp=EnrichedDealFile+".tmp";
   int h=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(h==INVALID_HANDLE) {
      Print("SniperGateTelemetry cannot write deals ",GetLastError());
      return;
   }
   FileWrite(h,"ticket","time","symbol","type","entry","reason",
             "magic","position_id","profit");
   int total=HistoryDealsTotal();
   int matched=0;
   for(int j=0;j<total;j++) {
      ulong id=HistoryDealGetTicket(j);
      if(id==0) continue;
      string symbol=HistoryDealGetString(id,DEAL_SYMBOL);
      long magic=HistoryDealGetInteger(id,DEAL_MAGIC);
      if(StringFind(symbol,"XAUUSD")!=0 || magic!=(long)SniperMagicNumber) continue;
      long type=HistoryDealGetInteger(id,DEAL_TYPE);
      long entry=HistoryDealGetInteger(id,DEAL_ENTRY);
      long reason=HistoryDealGetInteger(id,DEAL_REASON);
      if(type!=(long)DEAL_TYPE_BUY && type!=(long)DEAL_TYPE_SELL) continue;
      // Keep all entry/exit records to make the export auditable.
      FileWrite(h,(string)id,
                TimeToString((datetime)HistoryDealGetInteger(id,DEAL_TIME),TIME_DATE|TIME_SECONDS),
                symbol,SideLabel(type),EntryLabel(entry),ReasonLabel(reason),
                (string)magic,
                (string)HistoryDealGetInteger(id,DEAL_POSITION_ID),
                DoubleToString(HistoryDealGetDouble(id,DEAL_PROFIT),2));
      matched++;
   }
   FileClose(h);
   FileSwap(tmp,EnrichedDealFile);
}
void PublishIdentity() {
   string tmp=IdentityFile+".tmp";
   int h=FileOpen(tmp,FILE_WRITE|FILE_CSV|FILE_COMMON|FILE_ANSI,';');
   if(h==INVALID_HANDLE) return;
   long server_now=(long)TimeTradeServer();
   long utc_now=(long)TimeGMT();
   FileWrite(h,"account_login","server","symbol","magic","server_time",
             "utc_time","server_offset_seconds","generated_at_utc");
   FileWrite(h,(string)AccountInfoInteger(ACCOUNT_LOGIN),
             AccountInfoString(ACCOUNT_SERVER),_Symbol,
             (string)SniperMagicNumber,
             TimeToString((datetime)server_now,TIME_DATE|TIME_SECONDS),
             TimeToString((datetime)utc_now,TIME_DATE|TIME_SECONDS),
             (string)(server_now-utc_now),(string)utc_now);
   FileClose(h);
   FileSwap(tmp,IdentityFile);
}
void OnTimer() {
   if(StringFind(_Symbol,"XAUUSD")!=0) return;
   PublishBars();
   PublishDeals();
   PublishIdentity();
}
