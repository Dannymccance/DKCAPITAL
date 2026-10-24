// DKcapital V2: DEMO-only, single GBP Account 1, native three-leg XAUUSD execution.
// Not production approved. Compile in MetaEditor and broker-test BEFORE enabling entries.
// Historic messages never trigger an order after MaximumSignalAgeSeconds.
// Explicit provider close actions are independent of provider-reported TP/SL results.
#property strict
#property version "2.09"
#include <Trade/Trade.mqh>

input string FeedUrl = "http://127.0.0.1:8765/v3/demo/feed";
input string TelemetryUrl = "http://127.0.0.1:8765/v2/demo/telemetry";
input string FeedToken = "";
input string GoldSymbol = "XAUUSD";
input long ExpectedDemoLogin = 0;
input bool EnableNewEntries = false; // MUST remain false until accepted on demo.
input bool EnableLayerEntries = false; // Separate opt-in after broker-side layer tests.
// Provider-specific optional rules. Both remain OFF until isolated broker demo testing.
input bool EnableOpenRunnerEntries = false; // Only TP3 explicitly marked OPEN may use no native TP.
input bool EnableStormConditionalTP1 = false; // GWS STORM: TP1 only after 20 pips adverse.
input double StormPipSizeUSD = 0.10; // Explicit USD/pip conversion for GWS STORM.
input double StormDrawdownPips = 20.0;
input double MaxGroupRiskGBP = 75.0;
input double MaxSignalRiskPct = 4.0; // Maximum planned stop risk at the PROVIDER SL, across all TP legs and layer.
input double MaxPortfolioRiskGBP = 150.0;
input double RiskReferenceBalanceGBP = 1000.0; // Caps above are calibrated at this GBP balance.
input double RiskPaddingGBPPerLeg = 1.0;
input double StopSlippageUSD = 0.25;
input double EntryToleranceUSD = 1.0;
input double MaxSpreadUSD = 1.5;
input int MaximumSignalAgeSeconds = 180;
input int StormInitialFreshnessSeconds = 45; // Must be observed promptly to arm a new STORM setup.
input int StormEntryZoneWaitSeconds = 300; // Once armed, permit up to 5 minutes for price to enter zone.
input int MaximumConcurrentGroups = 3; // proposed limit; needs final signoff
input int PollSeconds = 3;
input int TelemetryEverySeconds = 30;
input int DeviationPoints = 35;
input double DailyLossWarningPct = 2.0; // ALERT ONLY: no daily loss entry/layer lock.

CTrade trade;
bool in_cycle = false;
bool telemetry_healthy = false;
int last_feed_http = 0;
datetime last_telemetry = 0;
bool telemetry_urgent = false;
const ulong MAGIC_BASE = 5100000000;
const ulong MAGIC_MAX = 48049672950;
const double INITIAL_GROUP_RISK_SHARE = 0.85;
const double LAYER_GROUP_RISK_SHARE = 0.15;
const int LAYER_LEG = 4;
const int TP4_LEG = 5; // Preserve pre-existing layer magic 4 on broker trades.

// Bounded, broker-sourced decisions. Never call a feed-only price rejection a
// broker rejection; record the EA gate that actually prevented submission.
struct ExecutionDecision
{
   string id;
   string stage;
   string code;
   string priorCode;
   datetime checked;
   double bid;
   double ask;
   long age;
   uint brokerCode;
};
ExecutionDecision decisionLog[];

void RecordDecision(const string id,const string stage,const string code,
                    const long age=0,const uint brokerCode=0)
{
   datetime now=TimeGMT();
   if(StringLen(id)<1 || StringLen(id)>64) return;
   for(int i=ArraySize(decisionLog)-1;i>=0;i--)
   {
      if(decisionLog[i].id==id && decisionLog[i].stage==stage)
      {
         if(decisionLog[i].code==code && now-decisionLog[i].checked<20) return;
         MqlTick q;
         bool quoted=SymbolInfoTick(GoldSymbol,q);
         if(code=="ENTRY_WINDOW_EXPIRED" && decisionLog[i].code!="ENTRY_WINDOW_EXPIRED")
            decisionLog[i].priorCode=decisionLog[i].code;
         decisionLog[i].code=code;
         decisionLog[i].checked=now;
         decisionLog[i].bid=quoted?q.bid:0;
         decisionLog[i].ask=quoted?q.ask:0;
         decisionLog[i].age=age>0?age:0;
         decisionLog[i].brokerCode=brokerCode;
         telemetry_urgent=true;
         Print("DK2 DECISION ",stage," signal=",id," code=",code,
               " age=",age," brokerCode=",brokerCode);
         return;
      }
   }
   int n=ArraySize(decisionLog);
   if(n>=120)
   {
      for(int j=1;j<n;j++) decisionLog[j-1]=decisionLog[j];
      n=119;
      ArrayResize(decisionLog,n);
   }
   ArrayResize(decisionLog,n+1);
   decisionLog[n].id=id;
   decisionLog[n].stage=stage;
   decisionLog[n].code=code;
   decisionLog[n].priorCode="";
   decisionLog[n].checked=now;
   MqlTick q;
   bool quoted=SymbolInfoTick(GoldSymbol,q);
   decisionLog[n].bid=quoted?q.bid:0;
   decisionLog[n].ask=quoted?q.ask:0;
   decisionLog[n].age=age>0?age:0;
   decisionLog[n].brokerCode=brokerCode;
   telemetry_urgent=true;
   Print("DK2 DECISION ",stage," signal=",id," code=",code,
         " age=",age," brokerCode=",brokerCode);
}

int UKDateKey()
{
   datetime utc=TimeGMT();
   MqlDateTime t;
   TimeToStruct(utc,t);
   MqlDateTime last=t;
   last.mon=3;last.day=31;last.hour=1;last.min=0;last.sec=0;
   datetime march=StructToTime(last);
   MqlDateTime m; TimeToStruct(march,m);
   march-=(int)m.day_of_week*86400;
   last.mon=10;
   datetime october=StructToTime(last);
   MqlDateTime o; TimeToStruct(october,o);
   october-=(int)o.day_of_week*86400;
   MqlDateTime local;
   TimeToStruct(utc+(utc>=march && utc<october?3600:0),local);
   return local.year*10000+local.mon*100+local.day;
}

// Informational alert ONLY. A breach NEVER gates new entries or layers.
// The baseline is the first observed balance for each Isle of Man day,
// persisted across EA restarts. No forced position closes or size changes.
bool DailyEquityWarningActive()
{
   int day=UKDateKey();
   string startKey=Key("DAILY_START_"+IntegerToString(day));
   string warnedKey=Key("DAILY_WARN_"+IntegerToString(day));
   if(GlobalVariableCheck(warnedKey)) return true;
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance<=0 || equity<=0 || !MathIsValidNumber(balance) || !MathIsValidNumber(equity))
      return false;
   if(!GlobalVariableCheck(startKey))
   {
      GlobalVariableSet(startKey,balance);
      GlobalVariablesFlush();
      Print("DK2 daily loss alert baseline GBP=",balance," day=",day);
   }
   double opening=GlobalVariableGet(startKey);
   if(opening<=0) return false;
   if(equity<=opening*(1.0-DailyLossWarningPct/100.0))
   {
      GlobalVariableSet(warnedKey,(double)TimeGMT());
      GlobalVariablesFlush();
      telemetry_urgent=true;
      Print("DK2 DAILY EQUITY LOSS WARNING ONLY (trading continues) day=",day,
            " baseline=",opening," equity=",equity);
      return true;
   }
   return false;
}

string DealReasonName(const long reason)
{
   if(reason==DEAL_REASON_TP) return "TP";
   if(reason==DEAL_REASON_SL) return "SL";
   if(reason==DEAL_REASON_CLIENT) return "CLIENT";
   if(reason==DEAL_REASON_MOBILE) return "MOBILE";
   if(reason==DEAL_REASON_WEB) return "WEB";
   if(reason==DEAL_REASON_EXPERT) return "EXPERT";
   if(reason==DEAL_REASON_SO) return "SO";
   return "UNVERIFIED";
}


uint Hash(const string value)
{
   uint h = 2166136261;
   for(int k=0; k<StringLen(value); k++)
      h = (h ^ (uint)StringGetCharacter(value,k)) * 16777619;
   return h;
}
ulong GroupBase(const string id) { return MAGIC_BASE + (ulong)Hash(id)*10; }
ulong LegMagic(const string id,int leg) { return GroupBase(id)+(ulong)leg; }
bool Owned(ulong magic)
{
   return magic>=MAGIC_BASE+1 && magic<=MAGIC_MAX+TP4_LEG &&
          magic%10>=1 && magic%10<=TP4_LEG;
}
string Key(const string name) { return "DK2_"+IntegerToString(ExpectedDemoLogin)+"_"+name; }
string IDKey(const string id,const string suffix)
{
   return Key(IntegerToString((long)Hash(id))+"_"+suffix);
}
bool DemoIdentity()
{
   return ExpectedDemoLogin>0 &&
      AccountInfoInteger(ACCOUNT_LOGIN)==ExpectedDemoLogin &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO &&
      AccountInfoInteger(ACCOUNT_MARGIN_MODE)==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING &&
      AccountInfoString(ACCOUNT_CURRENCY)=="GBP";
}
bool TradeAllowed()
{
   // Do not initiate new entries while any broker order might fill.
   return DemoIdentity() && telemetry_healthy && last_feed_http==200 &&
          OrdersTotal()==0 &&
          (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) &&
          (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
}
bool BrokerDone()
{
   return trade.ResultRetcode()==TRADE_RETCODE_DONE;
}
double LotStep() { return SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_STEP); }
double LotMin() { return SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MIN); }
double FloorLots(double lots)
{
   double step=LotStep();
   if(step<=0) return 0;
   return NormalizeDouble(MathFloor(lots/step+1e-7)*step,8);
}
double NormalizePrice(double price)
{
   return NormalizeDouble(price,(int)SymbolInfoInteger(GoldSymbol,SYMBOL_DIGITS));
}
bool StopValid(bool buy,double stop,const MqlTick &q)
{
   double dist=SymbolInfoInteger(GoldSymbol,SYMBOL_TRADE_STOPS_LEVEL)*
               SymbolInfoDouble(GoldSymbol,SYMBOL_POINT);
   return stop>0 && (buy ? stop<q.bid-dist : stop>q.ask+dist);
}
double ValidTP(bool buy,double target,const MqlTick &q)
{
   double dist=SymbolInfoInteger(GoldSymbol,SYMBOL_TRADE_STOPS_LEVEL)*
               SymbolInfoDouble(GoldSymbol,SYMBOL_POINT);
   if(target<=0 || (buy ? target<=q.bid+dist : target>=q.ask-dist))
      return 0;
   return NormalizePrice(target);
}
bool BetterStop(bool buy,double proposed,double current)
{
   if(proposed<=0) return false;
   if(current<=0) return true;
   double point=SymbolInfoDouble(GoldSymbol,SYMBOL_POINT);
   return buy ? proposed>current+point*0.5 : proposed<current-point*0.5;
}
double RiskGBP(bool buy,double lots,double entry,double sl)
{
   if(sl<=0 || lots<=0) return -1;
   ENUM_ORDER_TYPE side=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   double conservative=buy ? sl-StopSlippageUSD : sl+StopSlippageUSD;
   double pnl=0;
   if(!OrderCalcProfit(side,GoldSymbol,lots,entry,conservative,pnl))
      return -1;
   return MathMax(0,-pnl)+RiskPaddingGBPPerLeg;
}
// Conservative scaling uses the lower of balance and equity during drawdowns.
double RiskBalanceGBP()
{
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance<=0 || equity<=0 || !MathIsValidNumber(balance) || !MathIsValidNumber(equity)) return 0;
   return MathMin(balance,equity);
}
double SignalRiskLimitGBP()
{
   // Target lots remain determined by ACCOUNT_BALANCE; only the broker
   // stop-risk budget uses the lower balance/equity on a drawdown.
   // Includes original TP1/TP2/TP3/TP4 legs and any attributable layer.
   return RiskBalanceGBP()*MaxSignalRiskPct/100.0;
}
double GroupRiskLimitGBP()
{
   return MaxGroupRiskGBP*RiskBalanceGBP()/RiskReferenceBalanceGBP;
}
double PortfolioRiskLimitGBP()
{
   return MaxPortfolioRiskGBP*RiskBalanceGBP()/RiskReferenceBalanceGBP;
}
double PositionRiskGBP()
{
   double sl=PositionGetDouble(POSITION_SL);
   if(sl<=0) return -1;
   return RiskGBP(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY,
         PositionGetDouble(POSITION_VOLUME),
         PositionGetDouble(POSITION_PRICE_OPEN),sl);
}
bool Exposure(double &portfolio,double &groupRisk,const ulong root,int &groups)
{
   portfolio=0;groupRisk=0; groups=0;
   ulong roots[];
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || PositionGetString(POSITION_SYMBOL)!=GoldSymbol) continue;
      ulong magic=(ulong)PositionGetInteger(POSITION_MAGIC);
      if(!Owned(magic))  // Unexpected/mixed XAUUSD positions require manual review.
      {
         Print("DK2 RISK BLOCK: unknown XAUUSD ticket=",ticket);
         return false;
      }
      double risk=PositionRiskGBP();
      if(risk<0)
      {
         Print("DK2 RISK BLOCK: missing or invalid broker native SL ticket=",ticket);
         return false;
      }
      portfolio+=risk;
      ulong group=magic-magic%10;
      if(group==root) groupRisk+=risk;
      bool found=false;
      for(int j=0;j<ArraySize(roots);j++) if(roots[j]==group) found=true;
      if(!found)
      {
         int n=ArraySize(roots);ArrayResize(roots,n+1);roots[n]=group;groups++;
      }
   }
   return true;
}
bool IsUKFriday()
{
   // Europe/Isle_of_Man: DST between last Sunday March 01:00 UTC
   // and last Sunday October 01:00 UTC.
   datetime utc=TimeGMT();
   MqlDateTime now;TimeToStruct(utc,now);
   MqlDateTime t=now;t.mon=3;t.day=31;t.hour=1;t.min=0;t.sec=0;
   datetime march=StructToTime(t);
   MqlDateTime m;TimeToStruct(march,m);
   march-=(int)m.day_of_week*86400;
   t.mon=10;t.day=31;
   datetime october=StructToTime(t);
   MqlDateTime o;TimeToStruct(october,o);
   october-=(int)o.day_of_week*86400;
   bool dst=utc>=march && utc<october;
   MqlDateTime local;TimeToStruct(utc+(dst?3600:0),local);
   return local.day_of_week==5;
}
bool Allocate(double total,double &a,double &b,double &c,const bool twoTargets)
{
   double step=LotStep(),minlot=LotMin();
   if(step<=0 || minlot<=0) return false;
   int count=(int)MathFloor(total/step+1e-6);
   int each=(int)MathCeil(minlot/step-1e-6);
   if(each<1) each=1;
   if(count<3*each) return false;
   int units[3];units[0]=each;units[1]=each;units[2]=each;
   // Three-target goal: TP1=25%, TP2=50%, TP3=25%.
   // Two-target goal: TP1=25%, TP2=75%, split TP2 across two
   // independently stopped native legs so TP1 fill still drives BE.
   double w[3];w[0]=0.25;
   w[1]=twoTargets?0.375:0.50;
   w[2]=twoTargets?0.375:0.25;
   for(int k=3*each;k<count;k++)
   {
      int best=2;
      double gap=-1e10;
      for(int j=2;j>=0;j--)
      {
         double missing=w[j]*count-units[j];
         if(missing>gap+1e-7) {gap=missing;best=j;}
      }
      units[best]++;
   }
   a=NormalizeDouble(units[0]*step,8);
   b=NormalizeDouble(units[1]*step,8);
   c=NormalizeDouble(units[2]*step,8);
   if(a<minlot-1e-7 || b<minlot-1e-7 || c<minlot-1e-7)
      return false;
   // Broker volume-step limits can prevent 25/50/25 on tiny trades.
   // Never misrepresent an equal 1/1/1 three-target exit as TP2-majority.
   // Two-target trades CAN honour the priority with minimum 1/1/1,
   // because legs 2 and 3 are assigned to TP2.
   return twoTargets ? b+c>a+1e-7 : (b>a+1e-7 && b>c+1e-7);
}
bool AllocateFour(double total,double &a,double &b,double &c,double &d)
{
   double step=LotStep(),minlot=LotMin();
   if(step<=0 || minlot<=0) return false;
   int count=(int)MathFloor(total/step+1e-6);
   int each=(int)MathCeil(minlot/step-1e-6);
   if(each<1) each=1;
   // Four native targets: 20%, 40%, 20%, 20%, TP2 strictly largest.
   if(count<5*each) return false;
   int units[4];for(int i=0;i<4;i++) units[i]=each;
   double w[4];w[0]=0.20;w[1]=0.40;w[2]=0.20;w[3]=0.20;
   for(int k=4*each;k<count;k++)
   {
      int best=3;double gap=-1e10;
      for(int j=3;j>=0;j--)
      {
         double missing=w[j]*count-units[j];
         if(missing>gap+1e-7) {gap=missing;best=j;}
      }
      units[best]++;
   }
   a=NormalizeDouble(units[0]*step,8);
   b=NormalizeDouble(units[1]*step,8);
   c=NormalizeDouble(units[2]*step,8);
   d=NormalizeDouble(units[3]*step,8);
   return b>a+1e-7 && b>c+1e-7 && b>d+1e-7;
}
bool HasBeenAttempted(const string id)
{
   if(GlobalVariableCheck(IDKey(id,"ATTEMPT"))) return true;
   ulong root=GroupBase(id);
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      if(OrderGetTicket(i)>0)
      {
         ulong m=(ulong)OrderGetInteger(ORDER_MAGIC);
         if(Owned(m) && m-m%10==root) return true;
      }
   }
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)>0)
      {
         ulong m=(ulong)PositionGetInteger(POSITION_MAGIC);
         if(Owned(m) && m-m%10==root) return true;
      }
   }
   // Broker history provides a second deduplication check if globals are lost.
   if(HistorySelect(TimeCurrent()-86400*90,TimeCurrent()+60))
   {
      for(int i=HistoryOrdersTotal()-1;i>=0;i--)
      {
         ulong t=HistoryOrderGetTicket(i);
         ulong m=(ulong)HistoryOrderGetInteger(t,ORDER_MAGIC);
         if(Owned(m) && m-m%10==root) return true;
      }
      for(int i=HistoryDealsTotal()-1;i>=0;i--)
      {
         ulong d=HistoryDealGetTicket(i);
         ulong m=(ulong)HistoryDealGetInteger(d,DEAL_MAGIC);
         if(Owned(m) && m-m%10==root) return true;
      }
   }
   return false;
}
ulong TicketForLeg(const string id,int leg)
{
   ulong magic=LegMagic(id,leg);
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket>0 && PositionGetString(POSITION_SYMBOL)==GoldSymbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC)==magic)
         return ticket;
   }
   return 0;
}
bool CloseTicket(ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return true;
   trade.SetExpertMagicNumber((ulong)PositionGetInteger(POSITION_MAGIC));
   if(!trade.PositionClose(ticket) || !BrokerDone())
   {
      Print("DK2 ALERT close failed ticket=",ticket," retcode=",trade.ResultRetcode());
      return false;
   }
   return !PositionSelectByTicket(ticket);
}
bool CloseGroup(const string id)
{
   bool ok=true;
   for(int leg=1;leg<=TP4_LEG;leg++)
   {
      ulong ticket=TicketForLeg(id,leg);
      if(ticket>0 && !CloseTicket(ticket)) ok=false;
   }
   return ok;
}
bool TP1BrokerConfirmed(const string id)
{
   ulong magic=LegMagic(id,1);
   if(TicketForLeg(id,1)>0) return false;
   if(!HistorySelect(TimeCurrent()-86400*90,TimeCurrent()+3600)) return false;
   ulong openedId=0;
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong d=HistoryDealGetTicket(i);
      if((ulong)HistoryDealGetInteger(d,DEAL_MAGIC)==magic &&
         HistoryDealGetInteger(d,DEAL_ENTRY)==DEAL_ENTRY_IN)
      {
         openedId=(ulong)HistoryDealGetInteger(d,DEAL_POSITION_ID);
         break;
      }
   }
   if(openedId==0) return false;
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong d=HistoryDealGetTicket(i);
      if((ulong)HistoryDealGetInteger(d,DEAL_POSITION_ID)!=openedId) continue;
      if(HistoryDealGetInteger(d,DEAL_ENTRY)==DEAL_ENTRY_OUT &&
         HistoryDealGetInteger(d,DEAL_REASON)==DEAL_REASON_TP)
         return true;
   }
   return false;
}
bool IsStormSignal(const string id)
{
   return StringFind(id,"-1004334637102:")==0;
}
int SignalAgeLimit(const string id)
{
   // The first STORM observation must be fresh, but the zone may arrive later.
   return IsStormSignal(id) ? StormEntryZoneWaitSeconds : MaximumSignalAgeSeconds;
}
bool StormEntryArmed(const string id,const long age)
{
   if(!IsStormSignal(id)) return true;
   string key=IDKey(id,"STORM_ENTRY_ARMED");
   if(GlobalVariableCheck(key)) return true; // survives restart before the price arrives
   if(age<0 || age>StormInitialFreshnessSeconds || !EnableNewEntries ||
      !DemoIdentity() || !telemetry_healthy || last_feed_http!=200) return false;
   GlobalVariableSet(key,(double)TimeGMT());
   GlobalVariablesFlush();
   Print("DK2 STORM entry armed awaiting zone id=",id," age=",age,
         " expires_at=",TimeToString((datetime)(TimeGMT()+SignalAgeLimit(id)-age)));
   return true;
}
bool StormTP1DrawdownReached(const string id,const string direction)
{
   string key=IDKey(id,"STORM_DRAWDOWN_TP1");
   if(GlobalVariableCheck(key) && GlobalVariableGet(key)==1) return true;
   if(!GlobalVariableCheck(IDKey(id,"STORM_TP_POLICY"))) return false;
   ulong ticket=TicketForLeg(id,1);
   if(ticket==0 || !PositionSelectByTicket(ticket)) return false;
   MqlTick quote;
   if(!SymbolInfoTick(GoldSymbol,quote) || quote.bid<=0 || quote.ask<=0) return false;
   bool buy=direction=="BUY";
   double fill=PositionGetDouble(POSITION_PRICE_OPEN);
   // Observed adverse excursion at the poll time, not an assumed intrabar low.
   double adverse=buy ? fill-quote.bid : quote.ask-fill;
   double threshold=StormDrawdownPips*StormPipSizeUSD;
   if(threshold<=0 || adverse<threshold) return false;
   GlobalVariableSet(key,1);
   GlobalVariablesFlush();
   Print("DK2 STORM TP1 activated after observed drawdown id=",id,
         " adverseUSD=",adverse," thresholdUSD=",threshold);
   return true;
}
void ManageLegs(const string id,const string direction,const double stop,
                const string slMode,const double tp1,const double tp2,const double tp3,
                const double tp4,const bool openRunner)
{
   bool buy=direction=="BUY";
   bool tp1Confirmed=TP1BrokerConfirmed(id);
   bool stormAdaptive=IsStormSignal(id) &&
      GlobalVariableCheck(IDKey(id,"STORM_TP_POLICY"));
   // Persist the original two-target execution policy over gateway restarts
   // or later provider amendments; do not silently move the TP2 exit leg
   // to a newly added TP3 target.
   bool wasTwoTargets=GlobalVariableCheck(IDKey(id,"TWO_TARGET_ENTRY"));
   bool wasFourTargets=GlobalVariableCheck(IDKey(id,"FOUR_TARGET_ENTRY"));
   bool stormUseTP1=stormAdaptive && StormTP1DrawdownReached(id,direction);
   MqlTick quote;
   if(!SymbolInfoTick(GoldSymbol,quote)) return;
   for(int leg=1;leg<=TP4_LEG;leg++)
   {
      if(leg==TP4_LEG && !wasFourTargets) continue;
      ulong ticket=TicketForLeg(id,leg);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if((PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY)!=buy) continue;
      double currentSL=PositionGetDouble(POSITION_SL);
      double currentTP=PositionGetDouble(POSITION_TP);
      double wantedSL=currentSL;
      double fill=PositionGetDouble(POSITION_PRICE_OPEN);
      // Provider SL amendments are authoritative before BE, including
      // wider stops that exceed the initial entry risk budget. Entry risk
      // caps are intentionally checked only when opening a new position.
      bool slAlreadyAtOrBeyondBE=(currentSL>0 &&
                                   (buy ? currentSL>=fill : currentSL<=fill));
      bool preserveBE=(slMode=="BREAKEVEN" ||
                       (tp1Confirmed && leg!=1 && slMode!="DEFER_BE") ||
                       slAlreadyAtOrBeyondBE);
      if(preserveBE)
      {
         // Once a position is protected at BE (or TP1 has filled), provider
         // amendments may tighten the stop but must never widen it again.
         double proposed=fill;
         if(slMode=="PRICE" && BetterStop(buy,stop,proposed))
            proposed=stop;
         if(BetterStop(buy,proposed,wantedSL) && StopValid(buy,proposed,quote))
            wantedSL=NormalizePrice(proposed);
      }
      else if(slMode=="PRICE" && stop>0 && StopValid(buy,stop,quote))
      {
         // Explicit provider changes may increase risk on an existing trade.
         wantedSL=NormalizePrice(stop);
      }
      // The separately tracked 10% layer exits at TP2.
      double firstTarget=(stormAdaptive && !stormUseTP1)?tp2:tp1;
      double rawTP=leg==1?firstTarget:
                    (leg==2 || leg==LAYER_LEG || (leg==3 && wasTwoTargets)?tp2:
                    (leg==TP4_LEG?tp4:tp3));
      double validTP=ValidTP(buy,rawTP,quote);
      // Invalid or absent targets stay blank; never invent a TP.
      // Keep an existing valid native TP when a later feed omits it.
      double wantedTP=validTP>0?validTP:currentTP;
      bool slChanged=MathAbs(wantedSL-currentSL)>=1e-8;
      if(!slChanged && MathAbs(wantedTP-currentTP)<1e-8)
         continue;
      trade.SetExpertMagicNumber(LegMagic(id,leg));
      if(!trade.PositionModify(ticket,wantedSL,wantedTP) || !BrokerDone())
         Print("DK2 ALERT native SL/TP modification failed ticket=",ticket,
               " code=",trade.ResultRetcode());
      else if(slChanged)
         Print("DK2 native SL updated ticket=",ticket," old=",currentSL,
               " new=",wantedSL," provider_stop=",stop);
   }
}
void TryEntry(const string id,const string status,const string direction,
              double low,double high,double providerSL,const long opened,
              double tp1,double tp2,double tp3,double tp4,const bool openRunner)
{
   if(status!="ACTIVE") return;
   long age=(long)TimeGMT()-opened;
   int maxAge=SignalAgeLimit(id);
   if(age<0 || age>maxAge)
   {
      if(age>maxAge && age<maxAge+120 &&
         !HasBeenAttempted(id))
         RecordDecision(id,"ENTRY","ENTRY_WINDOW_EXPIRED",age);
      return;
   }
   if(!EnableNewEntries) {RecordDecision(id,"ENTRY","ENTRY_DISABLED",age);return;}
   // The first STORM observation must arrive within 45 seconds while the
   // demo feed is healthy. After arming, repeated polls can wait up to
   // 5 minutes from the provider's original timestamp for the actual zone.
   if(!StormEntryArmed(id,age))
   {RecordDecision(id,"ENTRY","STORM_SIGNAL_NOT_ARMED",age);return;}
   if(!DemoIdentity()) {RecordDecision(id,"ENTRY","DEMO_IDENTITY_FAILED",age);return;}
   if(OrdersTotal()>0) {RecordDecision(id,"ENTRY","BROKER_PENDING_ORDERS",age);return;}
   if(!telemetry_healthy || last_feed_http!=200)
   {RecordDecision(id,"ENTRY","GATEWAY_OR_TELEMETRY_UNHEALTHY",age);return;}
   if(!(bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !(bool)MQLInfoInteger(MQL_TRADE_ALLOWED))
   {RecordDecision(id,"ENTRY","MT5_TRADING_DISABLED",age);return;}
   if(HasBeenAttempted(id)) return;
   MqlTick q;
   if(!SymbolInfoTick(GoldSymbol,q) || q.ask<=0 || q.bid<=0)
   {RecordDecision(id,"ENTRY","BROKER_QUOTE_UNAVAILABLE",age);return;}
   if(q.ask-q.bid>MaxSpreadUSD)
   {RecordDecision(id,"ENTRY","BROKER_SPREAD_TOO_WIDE",age);return;}
   if((long)TimeTradeServer()-(long)q.time>30)
   {RecordDecision(id,"ENTRY","BROKER_QUOTE_STALE",age);return;}
   bool buy=direction=="BUY";
   double fill=buy?q.ask:q.bid;
   double zLow=MathMin(low,high),zHigh=MathMax(low,high);
   // STORM uses the exact provider entry zone: for BUY 4001-4003
   // with broker ask 3998, keep armed until ask first reaches >=4001.
   // The other providers retain the current +/-$1.00 tolerance.
   double allowedLow=IsStormSignal(id)?zLow:zLow-EntryToleranceUSD;
   double allowedHigh=IsStormSignal(id)?zHigh:zHigh+EntryToleranceUSD;
   if(fill<allowedLow || fill>allowedHigh)
   {RecordDecision(id,"ENTRY","PRICE_OUTSIDE_ENTRY_ZONE",age);return;}
   // Provider's SL is the ONLY permitted entry stop.
   // Risk limits adjust lots, never the protective stop price.
   // Missing/wrong-side/broker-invalid SL means skip until corrected.
   if(providerSL<=0 || !StopValid(buy,providerSL,q) ||
      (buy ? providerSL>=zLow : providerSL<=zHigh))
   {
      RecordDecision(id,"ENTRY","PROVIDER_SL_INVALID_OR_MISSING",age);
      Print("DK2 ENTRY SKIP: missing/invalid provider SL id=",id,
            " providerSL=",providerSL);
      return;
   }
   // Do not open an unintended runner when Telegram target numbering is
   // malformed or a native target is already behind the broker quote.
   // A provider may define exactly two targets. TP3=0 denotes "absent",
   // never OPEN. Only the literal OPEN signal may opt into a runner.
   bool oneTarget=!openRunner && tp2<=0 && tp3<=0 && tp4<=0;
   bool twoTargets=!openRunner && tp2>0 && tp3<=0 && tp4<=0;
   bool fourTargets=!openRunner && tp2>0 && tp3>0 && tp4>0;
   int initialLegCount=oneTarget?1:(fourTargets?4:3);
   if(tp1<=0 || ValidTP(buy,tp1,q)<=0 ||
      (!oneTarget && (tp2<=0 || ValidTP(buy,tp2,q)<=0)) ||
      (!oneTarget && !twoTargets && !openRunner &&
       (tp3<=0 || ValidTP(buy,tp3,q)<=0)) ||
      (fourTargets && ValidTP(buy,tp4,q)<=0) ||
      (tp4>0 && !fourTargets) ||
      (tp3<=0 && tp2>0 && !twoTargets && !openRunner))
   {RecordDecision(id,"ENTRY","MISSING_OR_INVALID_NATIVE_TP",age);return;}
   if(openRunner && !EnableOpenRunnerEntries)
   {RecordDecision(id,"ENTRY","OPEN_RUNNER_REQUIRES_OPT_IN",age);return;}
   double sl=NormalizePrice(providerSL);
   if(!StopValid(buy,sl,q))
   {RecordDecision(id,"ENTRY","BROKER_SL_INVALID",age);return;}
   double portfolio=0,groupRisk=0;
   int groups=0;
   if(!Exposure(portfolio,groupRisk,GroupBase(id),groups))
   {RecordDecision(id,"ENTRY","BROKER_EXPOSURE_UNSAFE",age);return;}
   if(MaximumConcurrentGroups>0 && groups>=MaximumConcurrentGroups)
   {RecordDecision(id,"ENTRY","MAX_CONCURRENT_GROUPS",age);return;}
   // Reserve 15% of balance-scaled group capacity for a possible later layer.
   double groupLimit=GroupRiskLimitGBP(),portfolioLimit=PortfolioRiskLimitGBP();
   if(groupLimit<=0 || portfolioLimit<groupLimit)
   {RecordDecision(id,"ENTRY","INVALID_RISK_LIMITS",age);return;}
   // Keep the existing balance-based targetLots calculation below unchanged.
   // The 4% per-signal ceiling is a HARD planned-risk cap using the exact provider SL.
   // Do not reserve an unused percentage of the 4% signal cap for a
   // layer: it may use only whatever group stop-risk headroom actually remains.
   double signalLimit=SignalRiskLimitGBP();
   double signalRemaining=signalLimit-groupRisk;
   double budget=MathMin(groupLimit*INITIAL_GROUP_RISK_SHARE-groupRisk,
                         MathMin(portfolioLimit-portfolio,signalRemaining));
   if(budget<=initialLegCount*RiskPaddingGBPPerLeg)
   {
      RecordDecision(id,"ENTRY",
                     signalRemaining<=initialLegCount*RiskPaddingGBPPerLeg ?
                     "SIGNAL_RISK_TOO_SMALL_FOR_REQUIRED_LEGS" :
                     "GROUP_OR_PORTFOLIO_RISK_LIMIT",age);
      return;
   }
   double minlot=LotMin();
   double targetLots=FloorLots(MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/100)*0.01);
   if(IsUKFriday()) targetLots=MathMin(targetLots,0.03);
   targetLots=MathMin(targetLots,SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MAX)*initialLegCount);
   double a=0,b=0,c=0,d=0;
   double vols[4];
   bool possible=false;
   for(double total=targetLots;
       total>=initialLegCount*minlot-1e-8;total=FloorLots(total-LotStep()))
   {
      if(oneTarget) {a=total;b=0;c=0;d=0;}
      else if(fourTargets)
      {
         if(!AllocateFour(total,a,b,c,d)) continue;
      }
      else if(!Allocate(total,a,b,c,twoTargets)) continue;
      vols[0]=a;vols[1]=b;vols[2]=c;vols[3]=d;
      double r1=RiskGBP(buy,a,fill,sl);
      double r2=initialLegCount>1?RiskGBP(buy,b,fill,sl):0;
      double r3=initialLegCount>2?RiskGBP(buy,c,fill,sl):0;
      double r4=fourTargets?RiskGBP(buy,d,fill,sl):0;
      // OrderCalcProfit may fail for a broker contract/currency conversion.
      // Its -1 sentinel is NOT negative exposure or spare risk capacity.
      if(r1<0 || r2<0 || r3<0 || r4<0)
      {
         RecordDecision(id,"ENTRY","BROKER_RISK_VALUATION_FAILED",age);
         Print("DK2 RISK BLOCK: failed broker stop-risk valuation id=",id);
         return;
      }
      double planned=r1+r2+r3+r4;
      if(planned>budget+1e-7) continue;
      double requiredMargin=0,margin=0;
      bool marginOK=true;
      ENUM_ORDER_TYPE type=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
      for(int i=0;i<initialLegCount;i++)
      {
         if(!OrderCalcMargin(type,GoldSymbol,vols[i],fill,margin) || margin<0 || !MathIsValidNumber(margin))
         { marginOK=false;break; }
         requiredMargin+=margin;
      }
      if(!marginOK || AccountInfoDouble(ACCOUNT_MARGIN_FREE)<requiredMargin*1.1)
         continue;
      possible=true;break;
   }
   if(!possible)
   {
      // Distinguish a broker's minimum three-leg size exceeding the 4%
      // total stop-risk budget from an unrelated free-margin failure.
      double minA=0,minB=0,minC=0,minD=0;
      bool minAvailable=oneTarget?true:
         (fourTargets?
           AllocateFour(FloorLots(5*minlot),minA,minB,minC,minD):
           Allocate(FloorLots(3*minlot),minA,minB,minC,twoTargets));
      if(oneTarget) minA=LotMin();
      double minimumRisk=minAvailable ?
         RiskGBP(buy,minA,fill,sl)+
         (oneTarget?0:RiskGBP(buy,minB,fill,sl)+RiskGBP(buy,minC,fill,sl))+
         (fourTargets?RiskGBP(buy,minD,fill,sl):0) : -1;
      string reason="MINIMUM_LEG_SIZE_OR_MARGIN";
      if(minAvailable && minimumRisk>=0 && minimumRisk>budget+1e-7)
         reason="MINIMUM_LEG_SIZE_EXCEEDS_SIGNAL_RISK";
      else if(targetLots<initialLegCount*minlot-1e-8)
         reason="BALANCE_TARGET_BELOW_REQUIRED_MINIMUM_LOTS";
      else if(!oneTarget && !twoTargets &&
              targetLots<(fourTargets?5:4)*minlot-1e-8)
         reason="TP2_MAJORITY_REQUIRES_ADDITIONAL_LOT_STEP";
      RecordDecision(id,"ENTRY",reason,age);
      Print("DK2 RISK BLOCK signal=",id," capacityGBP=",budget,
            " signalCapGBP=",signalLimit," targetLots=",targetLots,
            " minimumRiskGBP=",minimumRisk," required legs/TP2 majority cannot fit");
      return;
   }
   // Durable intent before first broker request prevents duplicates on restart.
   bool stormAdaptive=EnableStormConditionalTP1 && IsStormSignal(id);
   if(stormAdaptive) GlobalVariableSet(IDKey(id,"STORM_TP_POLICY"),1);
   if(twoTargets) GlobalVariableSet(IDKey(id,"TWO_TARGET_ENTRY"),1);
   if(fourTargets) GlobalVariableSet(IDKey(id,"FOUR_TARGET_ENTRY"),1);
   GlobalVariableSet(IDKey(id,"ATTEMPT"),(double)TimeGMT());
   GlobalVariablesFlush();
   // Default STORM first leg to TP2; use TP1 only if subsequent observed
   // adverse excursion reaches the configured 20-pip threshold.
   double targets[4];targets[0]=stormAdaptive && !oneTarget?tp2:tp1;
   targets[1]=tp2;targets[2]=openRunner?0:(twoTargets?tp2:tp3);
   targets[3]=tp4;
   bool allGood=true;
   for(int i=0;i<initialLegCount;i++)
   {
      int leg=i==3?TP4_LEG:i+1;
      trade.SetExpertMagicNumber(LegMagic(id,leg));
      double tp=(i==2 && openRunner)?0:ValidTP(buy,targets[i],q);
      bool sent=buy?trade.Buy(vols[i],GoldSymbol,0,sl,tp,"DK2 "+IntegerToString((long)Hash(id))+"-"+IntegerToString(leg)):
                    trade.Sell(vols[i],GoldSymbol,0,sl,tp,"DK2 "+IntegerToString((long)Hash(id))+"-"+IntegerToString(leg));
      ulong ticket=TicketForLeg(id,leg);
      if(!sent || !BrokerDone() || ticket==0 || !PositionSelectByTicket(ticket) ||
         PositionGetDouble(POSITION_SL)<=0 ||
         MathAbs(PositionGetDouble(POSITION_VOLUME)-vols[i])>LotStep()/2)
      {
         RecordDecision(id,"ENTRY","BROKER_ORDER_REJECTED_OR_PARTIAL",age,trade.ResultRetcode());
         Print("DK2 CRITICAL incomplete entry id=",id," leg=",leg,
               " retcode=",trade.ResultRetcode());
         allGood=false;break;
      }
   }
   if(!allGood)
   {
      Print("DK2 CRITICAL attempting emergency unwind of partial entry ",id);
      if(!CloseGroup(id)) Print("DK2 CRITICAL residual hedging leg requires review ",id);
   }
   else
   {
      RecordDecision(id,"ENTRY","BROKER_FILLED",age);
      Print("DK2 execution filled id=",id," TP1_lots=",a,
            " TP2_lots=",b+(twoTargets?c:0),
            " TP3_lots=",(twoTargets?0:c),
            " TP4_lots=",d," numTargets=",
            oneTarget?1:(twoTargets?2:(fourTargets?4:3)));
   }
}
// Broker-history original leg volume is fixed even after partial closes.
double OriginalVolume(const string id)
{
   if(!HistorySelect(TimeCurrent()-86400*90,TimeCurrent()+3600)) return 0;
   double total=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=GoldSymbol ||
         HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
      ulong magic=(ulong)HistoryDealGetInteger(deal,DEAL_MAGIC);
      for(int leg=1;leg<=TP4_LEG;leg++)
         if(leg!=LAYER_LEG && magic==LegMagic(id,leg))
         {
            total+=HistoryDealGetDouble(deal,DEAL_VOLUME);
            break;
         }
   }
   return total;
}
// Broker history preserves the one-layer rule if terminal globals are lost.
string LayerMarker(const string groupId)
{
   return "DK2L"+IntegerToString((long)Hash(groupId));
}
bool LayerPreviouslyAttempted(const string id,const string groupId)
{
   // Limit additions across all original signals in the same GWS group.
   if(GlobalVariableCheck(IDKey(groupId,"GROUP_LAYER_ATTEMPT"))) return true;
   if(GlobalVariableCheck(IDKey(id,"LAYER_ATTEMPT"))) return true;
   if(TicketForLeg(id,LAYER_LEG)>0) return true;
   // Broker history/order comments also deduplicate sibling signals if a
   // terminal's global-variable store is lost.
   string marker=LayerMarker(groupId);
   for(int p=PositionsTotal()-1;p>=0;p--)
   {
      ulong position=PositionGetTicket(p);
      if(position==0) continue;
      ulong m=(ulong)PositionGetInteger(POSITION_MAGIC);
      if(Owned(m) && m%10==LAYER_LEG &&
         StringFind(PositionGetString(POSITION_COMMENT),marker)>=0) return true;
   }
   if(!HistorySelect(TimeCurrent()-86400*90,TimeCurrent()+3600)) return true;
   ulong magic=LegMagic(id,LAYER_LEG);
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong deal=HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
      ulong m=(ulong)HistoryDealGetInteger(deal,DEAL_MAGIC);
      if((m==magic) ||
         (Owned(m) && m%10==LAYER_LEG &&
          StringFind(HistoryDealGetString(deal,DEAL_COMMENT),marker)>=0))
         return true;
   }
   return false;
}
// No implicit layering: only a fresh, explicit and attributable L command.
// The permission boundary is an upper BUY price or lower SELL price.
void TryLayer(const string id,const long mid,const double percentage,
              const int maximum,const double boundary,const long sentAt,
              const string groupId,const string status,const string direction,
              const double tp2)
{
   if(status!="ACTIVE" || mid<=0 || maximum!=1 ||
      MathAbs(percentage-10.0)>1e-8 || groupId=="" ||
      (direction!="BUY" && direction!="SELL")) return;
   long age=(long)TimeGMT()-sentAt;
   // Layer commands are immediate amendments, not queued entry zones.
   if(sentAt<=0 || age<0 || age>MaximumSignalAgeSeconds) return;
   if(!EnableNewEntries || !EnableLayerEntries)
   {RecordDecision(id,"LAYER","LAYER_ENTRIES_DISABLED",age);return;}
   if(!TradeAllowed())
   {RecordDecision(id,"LAYER","LAYER_MT5_SAFETY_GATE",age);return;}
   if(IsUKFriday())
   {RecordDecision(id,"LAYER","LAYER_FRIDAY_BLOCK",age);return;}
   if(LayerPreviouslyAttempted(id,groupId)) return;
   bool buy=direction=="BUY";
   MqlTick q;
   if(!SymbolInfoTick(GoldSymbol,q) || q.bid<=0 || q.ask<=0)
   {RecordDecision(id,"LAYER","BROKER_QUOTE_UNAVAILABLE",age);return;}
   if(q.ask-q.bid>MaxSpreadUSD)
   {RecordDecision(id,"LAYER","BROKER_SPREAD_TOO_WIDE",age);return;}
   if((long)TimeTradeServer()-(long)q.time>30)
   {RecordDecision(id,"LAYER","BROKER_QUOTE_STALE",age);return;}
   double fill=buy?q.ask:q.bid;
   if(boundary>0 && (buy?fill>boundary:fill<boundary))
   {RecordDecision(id,"LAYER","LAYER_PRICE_BOUNDARY",age);return;}
   double referenceLots=OriginalVolume(id);
   double layerLots=FloorLots(referenceLots*0.10);
   if(referenceLots<=0 || layerLots<LotMin()-1e-8 ||
      layerLots>SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MAX))
   {RecordDecision(id,"LAYER","LAYER_BELOW_MINIMUM_LOT_OR_MISSING_ORIGINAL",age);return;}

   // No layer after TP1 or any missing original leg. On a four
   // target trade, the TP4 position is original leg 5, not the layer 4.
   bool fourTargets=GlobalVariableCheck(IDKey(id,"FOUR_TARGET_ENTRY"));
   double priceVolume=0,openVolume=0,protectiveSL=0;
   for(int leg=1;leg<=TP4_LEG;leg++)
   {
      if(leg==LAYER_LEG || (leg==TP4_LEG && !fourTargets)) continue;
      ulong ticket=TicketForLeg(id,leg);
      if(ticket==0 || !PositionSelectByTicket(ticket))
      {RecordDecision(id,"LAYER","LAYER_REQUIRES_ALL_ORIGINAL_LEGS_OPEN",age);return;}
      if((PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY)!=buy) return;
      double lots=PositionGetDouble(POSITION_VOLUME);
      double sl=PositionGetDouble(POSITION_SL);
      if(lots<=0 || sl<=0 || PositionRiskGBP()<0)
      {RecordDecision(id,"LAYER","LAYER_INVALID_NATIVE_SL_OR_RISK",age);return;}
      priceVolume+=PositionGetDouble(POSITION_PRICE_OPEN)*lots;
      openVolume+=lots;
      if(leg==1) protectiveSL=sl;
      else if(buy) protectiveSL=MathMax(protectiveSL,sl);
      else protectiveSL=MathMin(protectiveSL,sl);
   }
   if(openVolume<=0) return;
   double average=priceVolume/openVolume;
   double point=SymbolInfoDouble(GoldSymbol,SYMBOL_POINT);
   if(buy?fill>=average-point*0.5:fill<=average+point*0.5)
   {RecordDecision(id,"LAYER","LAYER_NOT_BETTER_PRICE",age);return;}
   if(!StopValid(buy,protectiveSL,q))
   {RecordDecision(id,"LAYER","LAYER_PROTECTIVE_SL_INVALID",age);return;}
   double plannedRisk=RiskGBP(buy,layerLots,fill,protectiveSL);
   if(plannedRisk<0 || plannedRisk>GroupRiskLimitGBP()*LAYER_GROUP_RISK_SHARE+1e-7)
   {RecordDecision(id,"LAYER","LAYER_RISK_LIMIT",age);return;}
   double portfolio=0,groupRisk=0;
   int groups=0;
   if(!Exposure(portfolio,groupRisk,GroupBase(id),groups))
   {RecordDecision(id,"LAYER","LAYER_BROKER_EXPOSURE_UNSAFE",age);return;}
   if(groupRisk+plannedRisk>SignalRiskLimitGBP()+1e-7)
   {RecordDecision(id,"LAYER","LAYER_EXCEEDS_SIGNAL_4_PERCENT_RISK",age);return;}
   if(groupRisk+plannedRisk>GroupRiskLimitGBP()+1e-7 ||
      portfolio+plannedRisk>PortfolioRiskLimitGBP()+1e-7)
   {RecordDecision(id,"LAYER","LAYER_GROUP_OR_PORTFOLIO_RISK_LIMIT",age);return;}
   double margin=0;
   ENUM_ORDER_TYPE type=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   if(!OrderCalcMargin(type,GoldSymbol,layerLots,fill,margin) ||
      margin<0 || !MathIsValidNumber(margin) ||
      AccountInfoDouble(ACCOUNT_MARGIN_FREE)<margin*1.1) return;
   double target=ValidTP(buy,tp2,q);
   if(target<=0)
   {RecordDecision(id,"LAYER","LAYER_TP2_NOT_VALID",age);return;} // No unplanned runner.

   // Persist before submission: a result of uncertain status needs manual
   // reconciliation, never a second automatic order.
   GlobalVariableSet(IDKey(id,"LAYER_ATTEMPT"),(double)TimeGMT());
   GlobalVariableSet(IDKey(groupId,"GROUP_LAYER_ATTEMPT"),(double)TimeGMT());
   GlobalVariablesFlush();
   trade.SetExpertMagicNumber(LegMagic(id,LAYER_LEG));
   bool submitted=buy?
      trade.Buy(layerLots,GoldSymbol,0,NormalizePrice(protectiveSL),target,
                LayerMarker(groupId)):
      trade.Sell(layerLots,GoldSymbol,0,NormalizePrice(protectiveSL),target,
                 LayerMarker(groupId));
   ulong ticket=TicketForLeg(id,LAYER_LEG);
   if(!submitted || !BrokerDone() || ticket==0 ||
      !PositionSelectByTicket(ticket) ||
      PositionGetDouble(POSITION_SL)<=0 ||
      MathAbs(PositionGetDouble(POSITION_VOLUME)-layerLots)>LotStep()/2)
   {
      RecordDecision(id,"LAYER","LAYER_BROKER_REJECTED_OR_UNCERTAIN",age,trade.ResultRetcode());
      Print("DK2 CRITICAL uncertain layer id=",id,
            " msg=",mid," ticket=",ticket,
            " brokerCode=",trade.ResultRetcode(),"; no automatic retry");
      if(ticket>0 && PositionSelectByTicket(ticket) &&
         PositionGetDouble(POSITION_SL)<=0 && !CloseTicket(ticket))
         Print("DK2 CRITICAL unprotected layer requires review ticket=",ticket);
      return;
   }
   RecordDecision(id,"LAYER","LAYER_BROKER_FILLED",age);
   Print("DK2 layer FILLED id=",id," telegram=",mid," ticket=",ticket,
         " lots=",layerLots," actualEntry=",PositionGetDouble(POSITION_PRICE_OPEN),
         " nativeSL=",PositionGetDouble(POSITION_SL),
         " nativeTP=",PositionGetDouble(POSITION_TP));
}
bool ActionKeySeen(const string id,const long mid,const string kind)
{
   string k=IDKey(id,kind+"_"+IntegerToString(mid));
   return GlobalVariableCheck(k) && GlobalVariableGet(k)==1;
}
void HandleAction(const string id,const long mid,const string kind,const double pct)
{
   if(mid<=0 || !DemoIdentity() || ActionKeySeen(id,mid,kind)) return;
   string key=IDKey(id,kind+"_"+IntegerToString(mid));
   if(kind=="CLOSE")
   {
      if(CloseGroup(id))
      {
         GlobalVariableSet(key,1);GlobalVariablesFlush();
         Print("DK2 provider explicit close applied id=",id," msg=",mid);
      }
      return;
   }
   if(kind!="PARTIAL_CLOSE" || pct<=0 || pct>100) return;
   // Keep a durable target remaining lot balance across retries and restarts.
   double total=0;
   for(int leg=1;leg<=TP4_LEG;leg++)
   {
      ulong t=TicketForLeg(id,leg);
      if(t>0 && PositionSelectByTicket(t))
         total+=PositionGetDouble(POSITION_VOLUME);
   }
   string targetKey=key+"_TARGET";
   if(!GlobalVariableCheck(targetKey))
   {
      double closeLots=FloorLots(total*pct/100);
      if(closeLots<LotMin()-1e-8 && total>0) return;
      GlobalVariableSet(targetKey,MathMax(0,total-closeLots));
      GlobalVariableSet(key,2);
      GlobalVariablesFlush();
   }
   double target=GlobalVariableGet(targetKey);
   double needed=FloorLots(total-target);
   for(int leg=TP4_LEG;leg>=1 && needed>=LotMin()-1e-8;leg--)
   {
      ulong t=TicketForLeg(id,leg);
      if(t==0 || !PositionSelectByTicket(t)) continue;
      double lots=PositionGetDouble(POSITION_VOLUME);
      double part=FloorLots(MathMin(needed,lots));
      if(part<LotMin()-1e-8) continue;
      if(lots-part>1e-8 && lots-part<LotMin()-1e-8) continue;
      trade.SetExpertMagicNumber(LegMagic(id,leg));
      bool done=part>=lots-1e-8 ? trade.PositionClose(t) :
                trade.PositionClosePartial(t,part);
      if(!done || !BrokerDone()) break;
      needed=FloorLots(needed-part);
   }
   double current=0;
   for(int leg=1;leg<=TP4_LEG;leg++)
   {
      ulong t=TicketForLeg(id,leg);
      if(t>0 && PositionSelectByTicket(t))
         current+=PositionGetDouble(POSITION_VOLUME);
   }
   if(current<=target+LotStep()/2)
   {
      GlobalVariableSet(key,1);
      GlobalVariablesFlush();
   }
   else Print("DK2 provider partial remains pending id=",id,
              " msg=",mid," target lots=",target," open lots=",current);
}
string N(double d,int digits=8) {return DoubleToString(d,digits);}
string L(long n) {return IntegerToString(n);}
string U(ulong n) {return IntegerToString((long)n);}
string IsoUTC(datetime t)
{
   MqlDateTime m;TimeToStruct(t,m);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d+00:00",
           m.year,m.mon,m.day,m.hour,m.min,m.sec);
}
void SendTelemetry()
{
   if(!DemoIdentity()) return;
   MqlTick q;
   if(!SymbolInfoTick(GoldSymbol,q) || q.bid<=0 || q.ask<=0) return;
   string json="{\"version\":2,\"mode\":\"DEMO\",\"currency\":\"GBP\",";
   json+="\"login\":"+L(ExpectedDemoLogin)+",\"generated_at\":\""+IsoUTC(TimeGMT())+"\",";
   json+="\"balance\":"+N(AccountInfoDouble(ACCOUNT_BALANCE),2)+",";
   json+="\"equity\":"+N(AccountInfoDouble(ACCOUNT_EQUITY),2)+",";
   json+="\"margin\":"+N(AccountInfoDouble(ACCOUNT_MARGIN),2)+",";
   json+="\"free_margin\":"+N(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2)+",";
   json+="\"bid\":"+N(q.bid,5)+",\"ask\":"+N(q.ask,5);
   json+=",\"feed_http\":"+IntegerToString(last_feed_http);
   json+=",\"entry_enabled\":"+(EnableNewEntries?"true":"false");
   json+=",\"layer_entries_enabled\":"+(EnableLayerEntries?"true":"false");
   json+=",\"daily_loss_warning\":"+(DailyEquityWarningActive()?"true":"false");
   json+=",\"daily_loss_guard_mode\":\"ALERT_ONLY\"";
   json+=",\"daily_loss_warning_pct\":"+N(DailyLossWarningPct,2);
   json+=",\"signal_risk_cap_pct\":"+N(MaxSignalRiskPct,3);
   json+=",\"signal_risk_cap_gbp\":"+N(SignalRiskLimitGBP(),2);
   json+=",\"daily_loss_day\":"+IntegerToString(UKDateKey());
   // All broker positions/orders must be counted, not only owned XAUUSD legs.
   int brokerCount=PositionsTotal(), unmanaged=0;
   bool allProtected=true;
   for(int p=brokerCount-1;p>=0;p--)
   {
      ulong pos=PositionGetTicket(p);
      if(pos==0) {allProtected=false;continue;}
      if(PositionGetDouble(POSITION_SL)<=0) allProtected=false;
      if(PositionGetString(POSITION_SYMBOL)!=GoldSymbol ||
         !Owned((ulong)PositionGetInteger(POSITION_MAGIC))) unmanaged++;
   }
   json+=",\"broker_positions_total\":"+IntegerToString(brokerCount);
   // Pending broker orders are account-wide, including unmatched orders.
   int brokerOrderCount=OrdersTotal(),unmanagedOrders=0;
   string ordersJson="[";
   bool firstOrder=true;
   for(int o=brokerOrderCount-1;o>=0;o--)
   {
      ulong orderTicket=OrderGetTicket(o);
      if(orderTicket==0) {unmanagedOrders++;continue;}
      ulong orderMagic=(ulong)OrderGetInteger(ORDER_MAGIC);
      string orderSymbol=OrderGetString(ORDER_SYMBOL);
      ENUM_ORDER_TYPE orderType=(ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      string orderKind="";
      if(orderType==ORDER_TYPE_BUY_LIMIT) orderKind="BUY_LIMIT";
      else if(orderType==ORDER_TYPE_BUY_STOP) orderKind="BUY_STOP";
      else if(orderType==ORDER_TYPE_SELL_LIMIT) orderKind="SELL_LIMIT";
      else if(orderType==ORDER_TYPE_SELL_STOP) orderKind="SELL_STOP";
      if(orderSymbol!=GoldSymbol || !Owned(orderMagic) || orderKind=="")
      {unmanagedOrders++;continue;}
      if(!firstOrder) ordersJson+=",";
      firstOrder=false;
      ordersJson+="{\"ticket\":"+U(orderTicket)+",\"magic\":"+U(orderMagic);
      ordersJson+=",\"symbol\":\""+orderSymbol+"\",\"type\":\""+orderKind+"\"";
      ordersJson+=",\"lots\":"+N(OrderGetDouble(ORDER_VOLUME_CURRENT));
      ordersJson+=",\"price\":"+N(OrderGetDouble(ORDER_PRICE_OPEN),5);
      ordersJson+=",\"sl\":"+N(OrderGetDouble(ORDER_SL),5);
      ordersJson+=",\"tp\":"+N(OrderGetDouble(ORDER_TP),5)+"}";
   }
   ordersJson+="]";
   json+=",\"broker_orders_total\":"+IntegerToString(brokerOrderCount);
   json+=",\"unmanaged_orders_count\":"+IntegerToString(unmanagedOrders);
   json+=",\"orders\":"+ordersJson;
   json+=",\"unmanaged_positions_count\":"+IntegerToString(unmanaged);
   json+=",\"broker_positions_protected\":"+(allProtected?"true":"false");
   json+=",\"positions\":[";
   bool first=true;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || PositionGetString(POSITION_SYMBOL)!=GoldSymbol) continue;
      ulong magic=(ulong)PositionGetInteger(POSITION_MAGIC);
      if(!Owned(magic)) continue;
      double r=PositionRiskGBP();
      if(!first) json+=",";
      first=false;
      json+="{\"ticket\":"+U(ticket)+",\"magic\":"+U(magic);
      json+=",\"side\":\""+(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?"BUY":"SELL")+"\"";
      json+=",\"lots\":"+N(PositionGetDouble(POSITION_VOLUME));
      json+=",\"entry\":"+N(PositionGetDouble(POSITION_PRICE_OPEN),5);
      json+=",\"sl\":"+N(PositionGetDouble(POSITION_SL),5);
      json+=",\"tp\":"+N(PositionGetDouble(POSITION_TP),5);
      json+=",\"profit\":"+N(PositionGetDouble(POSITION_PROFIT),2);
      json+=",\"swap\":"+N(PositionGetDouble(POSITION_SWAP),2);
      json+=",\"risk_gbp\":"+(r>=0?N(r,2):"null")+"}";
   }
   json+="],\"deals\":[";
   first=true;
   long offset=(long)TimeTradeServer()-(long)TimeGMT();
   if(MathAbs((double)offset)>14*3600) offset=0;
   if(HistorySelect(TimeCurrent()-86400*90,TimeCurrent()+3600))
   {
      // Native broker SL/TP closing deals may have magic=0.
      // Resolve ownership through their original IN deal's position ID.
      ulong mappedIds[],mappedMagics[];
      for(int i=0;i<HistoryDealsTotal();i++)
      {
         ulong d=HistoryDealGetTicket(i);
         if(d==0 || HistoryDealGetString(d,DEAL_SYMBOL)!=GoldSymbol ||
            HistoryDealGetInteger(d,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
         ulong m=(ulong)HistoryDealGetInteger(d,DEAL_MAGIC);
         if(!Owned(m)) continue;
         int n=ArraySize(mappedIds);
         ArrayResize(mappedIds,n+1);ArrayResize(mappedMagics,n+1);
         mappedIds[n]=(ulong)HistoryDealGetInteger(d,DEAL_POSITION_ID);
         mappedMagics[n]=m;
      }
      int emitted=0;
      for(int i=0;i<HistoryDealsTotal() && emitted<1000;i++)
      {
         ulong deal=HistoryDealGetTicket(i);
         if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=GoldSymbol) continue;
         ulong magic=(ulong)HistoryDealGetInteger(deal,DEAL_MAGIC);
         if(!Owned(magic))
         {
            ulong position=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
            for(int j=0;j<ArraySize(mappedIds);j++)
               if(mappedIds[j]==position) {magic=mappedMagics[j];break;}
         }
         if(!Owned(magic)) continue;
         long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
         if(entry!=DEAL_ENTRY_IN && entry!=DEAL_ENTRY_OUT) continue;
         ulong positionId=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
         if(positionId==0) continue;
         bool buy=HistoryDealGetInteger(deal,DEAL_TYPE)==DEAL_TYPE_BUY;
         datetime utcTime=(datetime)((long)HistoryDealGetInteger(deal,DEAL_TIME)-(long)offset);
         if(!first) json+=",";
         first=false;
         json+="{\"ticket\":"+U(deal)+",\"position_id\":"+U(positionId);
         json+=",\"magic\":"+U(magic);
         json+=",\"entry_type\":\""+(entry==DEAL_ENTRY_IN?"IN":"OUT")+"\"";
         json+=",\"side\":\""+(buy?"BUY":"SELL")+"\"";
         json+=",\"lots\":"+N(HistoryDealGetDouble(deal,DEAL_VOLUME));
         json+=",\"price\":"+N(HistoryDealGetDouble(deal,DEAL_PRICE),5);
         json+=",\"profit\":"+N(HistoryDealGetDouble(deal,DEAL_PROFIT),2);
         json+=",\"commission\":"+N(HistoryDealGetDouble(deal,DEAL_COMMISSION),2);
         json+=",\"swap\":"+N(HistoryDealGetDouble(deal,DEAL_SWAP),2);
         json+=",\"time\":\""+IsoUTC(utcTime)+"\"";
         json+=",\"reason\":\""+DealReasonName(HistoryDealGetInteger(deal,DEAL_REASON))+"\"}";

         emitted++;
      }
   }
   json+="],\"decisions\":[";
   bool firstDecision=true;
   for(int i=0;i<ArraySize(decisionLog);i++)
   {
      if(TimeGMT()-decisionLog[i].checked>86400) continue;
      if(!firstDecision) json+=",";
      firstDecision=false;
      json+="{\"signal_id\":\""+decisionLog[i].id+"\"";
      json+=",\"stage\":\""+decisionLog[i].stage+"\"";
      json+=",\"code\":\""+decisionLog[i].code+"\"";
      json+=",\"prior_code\":\""+decisionLog[i].priorCode+"\"";
      json+=",\"at\":\""+IsoUTC(decisionLog[i].checked)+"\"";
      json+=",\"bid\":"+N(decisionLog[i].bid,5);
      json+=",\"ask\":"+N(decisionLog[i].ask,5);
      json+=",\"age_seconds\":"+L(decisionLog[i].age);
      json+=",\"broker_retcode\":"+IntegerToString((long)decisionLog[i].brokerCode)+"}";
   }
   json+="]}";
   char payload[],response[];
   StringToCharArray(json,payload,0,WHOLE_ARRAY,CP_UTF8);
   if(ArraySize(payload)>0) ArrayResize(payload,ArraySize(payload)-1);
   string headers="Authorization: Bearer "+FeedToken+
     "\r\nContent-Type: application/json\r\n";
   string received;
   int status=WebRequest("POST",TelemetryUrl,headers,8000,payload,response,received);
   telemetry_healthy=(status==200);
   if(status!=200)
      Print("DK2 ALERT telemetry POST failed http=",status," error=",GetLastError());
}
void ProcessFeed(const string content)
{
   string lines[];
   int count=StringSplit(content,'\n',lines);
   if(count<1 || StringFind(lines[0],"DKDEMO3")!=0)
   {Print("DK2 ALERT invalid v2 instruction feed");return;}
   for(int i=1;i<count;i++)
   {
      string f[];StringTrimLeft(lines[i]);StringTrimRight(lines[i]);
      if(lines[i]=="") continue;
      int fields=StringSplit(lines[i],'|',f);
      if((fields==12 || fields==13) && f[0]=="S")
      {
         if(f[3]!="BUY" && f[3]!="SELL") continue;
         double low=StringToDouble(f[4]),high=StringToDouble(f[5]);
         if(low<=0 || high<=0) continue;
         bool openRunner=f[10]=="OPEN";
         if(fields==13 && openRunner) continue;
         double fourth=fields==13?StringToDouble(f[11]):0;
         long openedAt=(long)StringToInteger(f[fields-1]);
         TryEntry(f[1],f[2],f[3],low,high,StringToDouble(f[6]),
            openedAt,StringToDouble(f[8]),StringToDouble(f[9]),
            StringToDouble(f[10]),fourth,openRunner);
         ManageLegs(f[1],f[3],StringToDouble(f[6]),f[7],
            StringToDouble(f[8]),StringToDouble(f[9]),
            StringToDouble(f[10]),fourth,openRunner);
      }
      else if(fields==5 && f[0]=="A")
         HandleAction(f[1],StringToInteger(f[2]),f[3],StringToDouble(f[4]));
      else if(fields==8 && f[0]=="L")
      {
         // A layer needs a matching signal record in this exact feed response.
         for(int j=1;j<i;j++)
         {
            string prior[];
            int n=StringSplit(lines[j],'|',prior);
            if((n!=12 && n!=13) || prior[0]!="S" || prior[1]!=f[1])
               continue;
            TryLayer(f[1],(long)StringToInteger(f[2]),StringToDouble(f[3]),
                     (int)StringToInteger(f[4]),StringToDouble(f[5]),
                     (long)StringToInteger(f[6]),f[7],prior[2],prior[3],
                     StringToDouble(prior[9]));
            break;
         }
      }
   }
}
void OnTimer()
{
   if(in_cycle) return;
   in_cycle=true;
   if(!DemoIdentity()) {in_cycle=false;return;}
   if(TimeGMT()-last_telemetry>=TelemetryEverySeconds ||
      (telemetry_urgent && TimeGMT()-last_telemetry>=3))
   {
      SendTelemetry();last_telemetry=TimeGMT();
      if(telemetry_healthy) telemetry_urgent=false;
   }
   char req[],resp[];
   string headers="Authorization: Bearer "+FeedToken+"\r\n";
   string responseHeaders;
   int status=WebRequest("GET",FeedUrl,headers,8000,req,resp,responseHeaders);
   last_feed_http=status;
   if(status==200) ProcessFeed(CharArrayToString(resp,0,-1,CP_UTF8));
   else Print("DK2 ALERT instruction gateway unavailable http=",status,
              " error=",GetLastError());
   in_cycle=false;
}
int OnInit()
{
   if(!DemoIdentity())
   {Print("DK2 BLOCK requires approved GBP DEMO hedging login");return INIT_FAILED;}
   if(StringFind(FeedUrl,"http://127.0.0.1:8765/")!=0 ||
      StringFind(TelemetryUrl,"http://127.0.0.1:8765/")!=0 ||
      StringLen(FeedToken)<24 || PollSeconds<2 || TelemetryEverySeconds<10 ||
      MaxGroupRiskGBP<=0 || MaxPortfolioRiskGBP<MaxGroupRiskGBP ||
      MaxSignalRiskPct<=0 || MaxSignalRiskPct>100 ||
      DailyLossWarningPct<=0 || DailyLossWarningPct>100 ||
      RiskReferenceBalanceGBP<=0 || StormPipSizeUSD<=0 ||
      StormDrawdownPips<=0 || StormInitialFreshnessSeconds<=0 ||
      StormEntryZoneWaitSeconds<StormInitialFreshnessSeconds)
   {Print("DK2 invalid configuration");return INIT_PARAMETERS_INCORRECT;}
   if(!SymbolSelect(GoldSymbol,true)) return INIT_FAILED;
   trade.SetAsyncMode(false);
   trade.SetTypeFillingBySymbol(GoldSymbol);
   trade.SetDeviationInPoints(DeviationPoints);
   EventSetTimer(PollSeconds);
   Print("DK2 DEMO Account 1 ready entryEnabled=",EnableNewEntries,
         " login=",ExpectedDemoLogin," version=2.09 groupCapGBP=",GroupRiskLimitGBP(),
         " portfolioCapGBP=",PortfolioRiskLimitGBP());
   return INIT_SUCCEEDED;
}
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type==TRADE_TRANSACTION_DEAL_ADD ||
      trans.type==TRADE_TRANSACTION_POSITION ||
      trans.type==TRADE_TRANSACTION_ORDER_ADD ||
      trans.type==TRADE_TRANSACTION_ORDER_DELETE)
      telemetry_urgent=true;
}
void OnDeinit(const int reason) {EventKillTimer();}
