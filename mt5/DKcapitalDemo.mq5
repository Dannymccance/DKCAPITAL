// DKcapital demo-only XAUUSD execution EA (MQL5)
// Attach to an XAUUSD chart on each independent MT5 terminal.
// Requires the local gateway URL to be whitelisted in MT5 Tools > Options > Expert Advisors.
// Strict demo-account gate. No live-account execution path.
#property strict
#property version "1.00"
#include <Trade/Trade.mqh>

input string FeedUrl = "http://127.0.0.1:8765/v1/demo/feed";
input string FeedToken = "";
input string GoldSymbol = "XAUUSD";
input long ExpectedDemoLogin = 0;       // REQUIRED: the actual demo account number
input bool EnableNewEntries = false;    // switch on only after demo connectivity checks
input double TargetLots = 0.05;
input double MaxLossPerTradeFraction = 0.075; // 7.5% of equity, e.g. GBP75/GBP1000
input double DailyEquityDrawdownFraction = 0.10;
input double MaximumGoldSpread = 1.50;  // USD/oz
input double EntryTolerance = 1.00;     // USD/oz outside provider entry zone
input int MaximumSignalAgeSeconds = 180;
input int PollSeconds = 2;
input int DeviationPoints = 35;

CTrade trader;
bool in_cycle = false;

uint SignalHash(const string id)
{
   uint hash = 2166136261;
   for(int i=0; i<StringLen(id); i++)
      hash = (hash ^ (uint)StringGetCharacter(id,i)) * 16777619;
   return hash;
}
ulong SignalMagic(const string id) { return (ulong)4200000000 + (ulong)SignalHash(id); }
string SignalComment(const string id) { return "DK" + IntegerToString((long)SignalHash(id)); }
string LedgerKey(const ulong magic,const string field)
{
   return "DK"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"_"+IntegerToString((long)magic)+"_"+field;
}
bool OrderDone()
{
   uint rc = trader.ResultRetcode();
   return rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL;
}
double LotStep()
{
   return SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_STEP);
}
double FloorLots(double v)
{
   double step=LotStep();
   if(step<=0) return 0;
   return NormalizeDouble(MathFloor((v+1e-9)/step)*step,8);
}
bool DemoIdentity()
{
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
      return false;
   if(ExpectedDemoLogin<=0 || AccountInfoInteger(ACCOUNT_LOGIN)!=ExpectedDemoLogin)
      return false;
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
      return false;
   return true;
}
string DayKey()
{
   MqlDateTime t; TimeToStruct(TimeGMT(),t);
   return "DK"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+
          "D"+IntegerToString(t.year)+IntegerToString(t.mon)+IntegerToString(t.day);
}
bool DailyAllowed()
{
   string key=DayKey();
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   if(!GlobalVariableCheck(key))
      GlobalVariableSet(key,equity);
   double baseline=GlobalVariableGet(key);
   if(baseline<=0 || equity<=baseline*(1.0-DailyEquityDrawdownFraction))
   {
      Print("DKcapital: daily equity drawdown limit reached: baseline=",baseline," current=",equity);
      return false;
   }
   return true;
}
bool AlreadyTraded(const ulong magic)
{
   if(GlobalVariableCheck(LedgerKey(magic,"OPEN"))) return true;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket>0 && PositionGetInteger(POSITION_MAGIC)==(long)magic)
         return true;
   }
   if(HistorySelect(TimeCurrent()-86400*7,TimeCurrent()+600))
   {
      for(int i=HistoryOrdersTotal()-1;i>=0;i--)
      {
         ulong order=HistoryOrderGetTicket(i);
         if(order>0 && HistoryOrderGetInteger(order,ORDER_MAGIC)==(long)magic &&
            HistoryOrderGetInteger(order,ORDER_TYPE)<=ORDER_TYPE_SELL)
            return true;
      }
   }
   return false;
}
ulong OurPosition(const ulong magic)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket>0 && PositionGetString(POSITION_SYMBOL)==GoldSymbol &&
         PositionGetInteger(POSITION_MAGIC)==(long)magic)
         return ticket;
   }
   return 0;
}
bool BetterStop(const bool buy,double candidate,double current)
{
   if(candidate<=0) return false;
   if(current<=0) return true;
   return buy ? candidate>current+_Point : candidate<current-_Point;
}
bool TightenStop(ulong ticket,const double wanted)
{
   if(ticket==0 || !PositionSelectByTicket(ticket)) return false;
   bool buy=(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY);
   double current=PositionGetDouble(POSITION_SL);
   if(!BetterStop(buy,wanted,current)) return true;
   MqlTick tick;
   if(!SymbolInfoTick(GoldSymbol,tick)) return false;
   double point=SymbolInfoDouble(GoldSymbol,SYMBOL_POINT);
   double minDist=(double)SymbolInfoInteger(GoldSymbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   if(buy && wanted>=tick.bid-minDist) return false;
   if(!buy && wanted<=tick.ask+minDist) return false;
   double normalized=NormalizeDouble(wanted,(int)SymbolInfoInteger(GoldSymbol,SYMBOL_DIGITS));
   trader.SetExpertMagicNumber((ulong)PositionGetInteger(POSITION_MAGIC));
   if(!trader.PositionModify(ticket,normalized,PositionGetDouble(POSITION_TP)) || !OrderDone())
   {
      Print("DKcapital: stop modify failed ticket=",ticket," code=",trader.ResultRetcode());
      return false;
   }
   return true;
}
bool CloseFraction(ulong ticket,double requested,const bool all=false)
{
   if(!PositionSelectByTicket(ticket)) return false;
   double volume=PositionGetDouble(POSITION_VOLUME);
   double minimum=SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MIN);
   if(volume<=0) return false;
   trader.SetExpertMagicNumber((ulong)PositionGetInteger(POSITION_MAGIC));
   if(all || volume<=minimum+1e-9 || volume-requested<minimum-1e-9)
   {
      if(!trader.PositionClose(ticket) || !OrderDone())
      {
         Print("DKcapital: full close failed retcode=",trader.ResultRetcode());
         return false;
      }
      return true;
   }
   double part=FloorLots(requested);
   if(part+1e-9<minimum) return false;
   if(!trader.PositionClosePartial(ticket,part) || !OrderDone())
   {
      Print("DKcapital: partial close failed retcode=",trader.ResultRetcode());
      return false;
   }
   return true;
}
bool Fresh(const long opened)
{
   long age=(long)TimeGMT()-opened;
   return opened>0 && age>=0 && age<=MaximumSignalAgeSeconds;
}
void MaybeOpen(const string id,const string direction,const double low,const double high,
               double providerStop,const long opened)
{
   if(!EnableNewEntries || !DemoIdentity() || !DailyAllowed() || !Fresh(opened)) return;
   if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)==0 ||
      MQLInfoInteger(MQL_TRADE_ALLOWED)==0) return;
   if(!SymbolSelect(GoldSymbol,true)) return;

   ulong magic=SignalMagic(id);
   if(AlreadyTraded(magic)) return;
   MqlTick tick;
   if(!SymbolInfoTick(GoldSymbol,tick) || tick.ask<=0 || tick.bid<=0) return;
   if(tick.ask-tick.bid>MaximumGoldSpread) return;
   bool buy=(direction=="BUY");
   double fill=buy?tick.ask:tick.bid;
   double zoneLow=MathMin(low,high),zoneHigh=MathMax(low,high);
   if(fill<zoneLow-EntryTolerance || fill>zoneHigh+EntryTolerance) return;

   double buffer=MathMax(6.0,MathAbs(high-low)*2.0);
   double fallback=buy ? MathMin(zoneLow-buffer,fill-buffer) : MathMax(zoneHigh+buffer,fill+buffer);
   double stop=providerStop;
   if(buy && (stop>=zoneLow || stop>=fill)) stop=fallback;
   if(!buy && (stop<=zoneHigh || stop<=fill)) stop=fallback;
   if(stop<=0) return;

   double point=SymbolInfoDouble(GoldSymbol,SYMBOL_POINT);
   double minDist=(double)SymbolInfoInteger(GoldSymbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   if(buy && stop>=tick.bid-minDist) return;
   if(!buy && stop<=tick.ask+minDist) return;

   double lots=FloorLots(MathMin(TargetLots,SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MAX)));
   double minLots=SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MIN);
   double maxRisk=AccountInfoDouble(ACCOUNT_EQUITY)*MaxLossPerTradeFraction;
   double risk=0;
   ENUM_ORDER_TYPE orderType=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   while(lots+1e-9>=minLots)
   {
      double loss=0;
      if(!OrderCalcProfit(orderType,GoldSymbol,lots,fill,stop,loss)) return;
      risk=-loss;
      if(risk>0 && risk<=maxRisk) break;
      lots=FloorLots(lots-LotStep());
   }
   if(lots<minLots || risk<=0 || risk>maxRisk) return;
   if(AccountInfoDouble(ACCOUNT_MARGIN_FREE)<=0) return;

   trader.SetExpertMagicNumber(magic);
   trader.SetDeviationInPoints(DeviationPoints);
   trader.SetTypeFillingBySymbol(GoldSymbol);
   bool placed=buy?
       trader.Buy(lots,GoldSymbol,0,stop,0,SignalComment(id)):
       trader.Sell(lots,GoldSymbol,0,stop,0,SignalComment(id));
   if(!placed || !OrderDone())
   {
      Print("DKcapital entry failed ",id," retcode=",trader.ResultRetcode());
      return;
   }
   // Guard against a successful broker order whose protective stop is absent.
   ulong ticket=OurPosition(magic);
   if(ticket==0) { Print("DKcapital: order confirmed but position not found; reconciliation required ",id); return; }
   GlobalVariableSet(LedgerKey(magic,"OPEN"),1);
   GlobalVariableSet(LedgerKey(magic,"VOL"),PositionGetDouble(POSITION_VOLUME));
   GlobalVariableSet(LedgerKey(magic,"STAGE"),0);
   if(PositionGetDouble(POSITION_SL)<=0)
   {
      if(!TightenStop(ticket,stop))
      {
         Print("DKcapital: no confirmed protective SL: FLATTENING ",id);
         CloseFraction(ticket,0,true);
      }
   }
   Print("DKcapital demo entry ",id," lots=",lots," stop=",stop," estimated risk=",risk);
}
bool PassedTarget(const bool buy,const double target,const MqlTick &tick)
{
   if(target<=0) return false;
   return buy ? tick.bid>=target : tick.ask<=target;
}
void Manage(const string id,const string status,const string direction,
            const double providerStop,const string slMode,
            const double tp1,const double tp2,const double tp3,const double remaining)
{
   ulong magic=SignalMagic(id);
   ulong ticket=OurPosition(magic);
   if(ticket==0 || !PositionSelectByTicket(ticket)) return;
   bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
   if((buy && direction!="BUY") || (!buy && direction!="SELL")) return;
   if(status!="ACTIVE" || remaining<=0.00001)
   {
      if(CloseFraction(ticket,0,true)) Print("DKcapital closed from provider status id=",id);
      return;
   }

   double opened=PositionGetDouble(POSITION_PRICE_OPEN);
   if(slMode=="BREAKEVEN")
      TightenStop(ticket,opened);
   else if(providerStop>0)
      TightenStop(ticket,providerStop);

   MqlTick tick;
   if(!SymbolInfoTick(GoldSymbol,tick)) return;
   string stageKey=LedgerKey(magic,"STAGE");
   string volKey=LedgerKey(magic,"VOL");
   int stage=GlobalVariableCheck(stageKey)?(int)GlobalVariableGet(stageKey):0;
   double original=GlobalVariableCheck(volKey)?GlobalVariableGet(volKey):PositionGetDouble(POSITION_VOLUME);
   double minLot=SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MIN);
   if(stage==0 && PassedTarget(buy,tp1,tick))
   {
      double toClose=FloorLots(original*0.30);
      if(toClose<minLot) toClose=minLot;
      if(CloseFraction(ticket,toClose))
      {
         GlobalVariableSet(stageKey,1);
         TightenStop(OurPosition(magic),opened);
         Print("DKcapital TP1 filled ",id);
         stage=1;
      }
   }
   // Retry BE on every subsequent management cycle if initial modification failed.
   if(stage>=1) TightenStop(OurPosition(magic),opened);
   if(stage==1 && PassedTarget(buy,tp2,tick))
   {
      double toClose=FloorLots(original*0.30);
      if(toClose<minLot) toClose=minLot;
      if(CloseFraction(OurPosition(magic),toClose))
      {
         GlobalVariableSet(stageKey,2);
         Print("DKcapital TP2 filled ",id);
         stage=2;
      }
   }
   if(stage==2 && PassedTarget(buy,tp3,tick))
   {
      if(CloseFraction(OurPosition(magic),0,true))
      {
         GlobalVariableSet(stageKey,3);
         Print("DKcapital TP3 filled ",id);
      }
   }
}
void ProcessFeed(const string response)
{
   string lines[];
   int n=StringSplit(response,'\n',lines);
   if(n<1 || StringFind(lines[0],"DKDEMO1")!=0)
   {
      Print("DKcapital: invalid demo feed response");
      return;
   }
   for(int i=1;i<n;i++)
   {
      string row=lines[i];
      StringTrimLeft(row);StringTrimRight(row);
      if(row=="") continue;
      string f[];
      if(StringSplit(row,'|',f)!=13 || f[0]!="S") continue;
      string id=f[1],status=f[2],direction=f[3];
      if(direction!="BUY" && direction!="SELL") continue;
      double low=StringToDouble(f[4]),high=StringToDouble(f[5]);
      double stop=StringToDouble(f[6]);
      string slMode=f[7];
      double tp1=StringToDouble(f[8]),tp2=StringToDouble(f[9]),tp3=StringToDouble(f[10]);
      long opened=StringToInteger(f[11]);
      double remaining=StringToDouble(f[12]);
      if(low<=0 || high<=0) continue;
      if(status=="ACTIVE")
         MaybeOpen(id,direction,low,high,stop,opened);
      Manage(id,status,direction,stop,slMode,tp1,tp2,tp3,remaining);
   }
}
void OnTimer()
{
   if(in_cycle) return;
   in_cycle=true;
   if(!DemoIdentity())
   {
      Print("DKcapital DEMO SAFETY BLOCK: account mode/login/hedging mismatch");
      in_cycle=false;
      return;
   }
   char body[],response[];
   string headers="Authorization: Bearer "+FeedToken+"\r\nAccept: text/plain\r\n";
   string response_headers;
   ResetLastError();
   int code=WebRequest("GET",FeedUrl,headers,8000,body,response,response_headers);
   if(code!=200)
   {
      Print("DKcapital feed unavailable HTTP=",code," error=",GetLastError(),
            ". Add http://127.0.0.1:8765 to allowed WebRequest URLs.");
      in_cycle=false;
      return;
   }
   ProcessFeed(CharArrayToString(response,0,-1,CP_UTF8));
   in_cycle=false;
}
int OnInit()
{
   if(!DemoIdentity())
   {
      Print("DKcapital will only run on the explicitly approved DEMO hedging account.");
      return INIT_FAILED;
   }
   if(StringLen(FeedToken)<24 || TargetLots<=0 || MaxLossPerTradeFraction<=0 ||
      MaxLossPerTradeFraction>0.20 || DailyEquityDrawdownFraction<=0 ||
      DailyEquityDrawdownFraction>0.30 || PollSeconds<1)
   {
      Print("DKcapital: invalid EA settings or missing gateway token");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(!SymbolSelect(GoldSymbol,true)) return INIT_FAILED;
   trader.SetAsyncMode(false);
   trader.SetDeviationInPoints(DeviationPoints);
   EventSetTimer(PollSeconds);
   Print("DKcapital demo EA active; new entries=",EnableNewEntries,
         " lots target=",TargetLots," account=",ExpectedDemoLogin);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
