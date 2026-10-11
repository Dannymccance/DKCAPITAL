// DKcapital V2: DEMO-only, single GBP Account 1, native 3-leg XAUUSD execution.
// Not production approved. Compile in MetaEditor and broker-test BEFORE enabling entries.
// Historic messages never trigger an order after MaximumSignalAgeSeconds.
// Explicit provider close actions are independent of provider-reported TP/SL results.
#property strict
#property version "2.00"
#include <Trade/Trade.mqh>

input string FeedUrl = "http://127.0.0.1:8765/v2/demo/feed";
input string TelemetryUrl = "http://127.0.0.1:8765/v2/demo/telemetry";
input string FeedToken = "";
input string GoldSymbol = "XAUUSD";
input long ExpectedDemoLogin = 0;
input bool EnableNewEntries = false; // MUST remain false until accepted on demo.
input double MaxGroupRiskGBP = 75.0;
input double MaxPortfolioRiskGBP = 150.0;
input double RiskPaddingGBPPerLeg = 1.0;
input double StopSlippageUSD = 0.25;
input double EntryToleranceUSD = 1.0;
input double MaxSpreadUSD = 1.5;
input double HouseMinStopDistanceUSD = 6.0;
input int MaximumSignalAgeSeconds = 180;
input int MaximumConcurrentGroups = 3; // proposed limit; needs final signoff
input int PollSeconds = 3;
input int TelemetryEverySeconds = 30;
input int DeviationPoints = 35;

CTrade trade;
bool in_cycle = false;
bool telemetry_healthy = false;
int last_feed_http = 0;
datetime last_telemetry = 0;
const ulong MAGIC_BASE = 5100000000;
const ulong MAGIC_MAX = 48049672950;

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
   return magic>=MAGIC_BASE+1 && magic<=MAGIC_MAX+3 &&
          magic%10>=1 && magic%10<=3;
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
   return DemoIdentity() && telemetry_healthy && last_feed_http==200 &&
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
bool Allocate(double total,double &a,double &b,double &c)
{
   double step=LotStep(),minlot=LotMin();
   if(step<=0 || minlot<=0) return false;
   int count=(int)MathFloor(total/step+1e-6);
   int each=(int)MathCeil(minlot/step-1e-6);
   if(each<1) each=1;
   if(count<3*each) return false;
   int units[3];units[0]=each;units[1]=each;units[2]=each;
   double w[3];w[0]=0.3;w[1]=0.3;w[2]=0.4;
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
   return a>=minlot-1e-7 && b>=minlot-1e-7 && c>=minlot-1e-7;
}
bool HasBeenAttempted(const string id)
{
   if(GlobalVariableCheck(IDKey(id,"ATTEMPT"))) return true;
   ulong root=GroupBase(id);
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
   for(int leg=1;leg<=3;leg++)
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
   for(int i=HistoryDealsTotal()-1;i>=0;i--)
   {
      ulong deal=HistoryDealGetTicket(i);
      if((ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)!=magic) continue;
      if(HistoryDealGetInteger(deal,DEAL_ENTRY)==DEAL_ENTRY_OUT &&
         HistoryDealGetInteger(deal,DEAL_REASON)==DEAL_REASON_TP)
         return true;
   }
   return false;
}
void ManageLegs(const string id,const string direction,const double stop,
                const string slMode,const double tp1,const double tp2,const double tp3)
{
   bool buy=direction=="BUY";
   bool tp1Confirmed=TP1BrokerConfirmed(id);
   MqlTick quote;
   if(!SymbolInfoTick(GoldSymbol,quote)) return;
   for(int leg=1;leg<=3;leg++)
   {
      ulong ticket=TicketForLeg(id,leg);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if((PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY)!=buy) continue;
      double currentSL=PositionGetDouble(POSITION_SL);
      double currentTP=PositionGetDouble(POSITION_TP);
      double wantedSL=currentSL;
      double fill=PositionGetDouble(POSITION_PRICE_OPEN);
      if(slMode=="BREAKEVEN" || (tp1Confirmed && leg!=1))
      {
         if(BetterStop(buy,fill,wantedSL) && StopValid(buy,fill,quote))
            wantedSL=NormalizePrice(fill);
      }
      else if(BetterStop(buy,stop,wantedSL) && StopValid(buy,stop,quote))
         wantedSL=NormalizePrice(stop);
      double rawTP=leg==1?tp1:(leg==2?tp2:tp3);
      double validTP=ValidTP(buy,rawTP,quote);
      // Invalid or absent targets stay blank; never invent a TP.
      // Keep an existing valid native TP when a later feed omits it.
      double wantedTP=validTP>0?validTP:currentTP;
      if(MathAbs(wantedSL-currentSL)<1e-8 && MathAbs(wantedTP-currentTP)<1e-8)
         continue;
      trade.SetExpertMagicNumber(LegMagic(id,leg));
      if(!trade.PositionModify(ticket,wantedSL,wantedTP) || !BrokerDone())
         Print("DK2 ALERT native SL/TP modification failed ticket=",ticket,
               " code=",trade.ResultRetcode());
   }
}
void TryEntry(const string id,const string status,const string direction,
              double low,double high,double providerSL,const long opened,
              double tp1,double tp2,double tp3)
{
   if(!EnableNewEntries || status!="ACTIVE" || !TradeAllowed()) return;
   long age=(long)TimeGMT()-opened;
   if(age<0 || age>MaximumSignalAgeSeconds) return;
   if(HasBeenAttempted(id)) return;
   MqlTick q;
   if(!SymbolInfoTick(GoldSymbol,q) || q.ask<=0 || q.bid<=0 ||
      q.ask-q.bid>MaxSpreadUSD ||
      (long)TimeTradeServer()-(long)q.time>30) return;
   bool buy=direction=="BUY";
   double fill=buy?q.ask:q.bid;
   double zLow=MathMin(low,high),zHigh=MathMax(low,high);
   if(fill<zLow-EntryToleranceUSD || fill>zHigh+EntryToleranceUSD) return;
   double fallbackBuffer=MathMax(HouseMinStopDistanceUSD,MathAbs(zHigh-zLow)*2);
   double sl=providerSL;
   if(!StopValid(buy,sl,q) || (buy?sl>=zLow:sl<=zHigh))
      sl=buy?MathMin(zLow-fallbackBuffer,fill-fallbackBuffer):
             MathMax(zHigh+fallbackBuffer,fill+fallbackBuffer);
   sl=NormalizePrice(sl);
   if(!StopValid(buy,sl,q)) return;
   double portfolio=0,groupRisk=0;
   int groups=0;
   if(!Exposure(portfolio,groupRisk,GroupBase(id),groups)) return;
   if(MaximumConcurrentGroups>0 && groups>=MaximumConcurrentGroups) return;
   double budget=MathMin(MaxGroupRiskGBP-groupRisk,MaxPortfolioRiskGBP-portfolio);
   if(budget<=3*RiskPaddingGBPPerLeg) return;
   double minlot=LotMin();
   double targetLots=FloorLots(MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/100)*0.01);
   if(IsUKFriday()) targetLots=MathMin(targetLots,0.03);
   targetLots=MathMin(targetLots,SymbolInfoDouble(GoldSymbol,SYMBOL_VOLUME_MAX)*3);
   double a=0,b=0,c=0;
   double vols[3];
   bool possible=false;
   for(double total=targetLots;total>=3*minlot-1e-8;total=FloorLots(total-LotStep()))
   {
      if(!Allocate(total,a,b,c)) continue;
      vols[0]=a;vols[1]=b;vols[2]=c;
      double planned=RiskGBP(buy,a,fill,sl)+RiskGBP(buy,b,fill,sl)+RiskGBP(buy,c,fill,sl);
      if(planned>budget+1e-7) continue;
      double requiredMargin=0,margin=0;
      bool marginOK=true;
      ENUM_ORDER_TYPE type=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
      for(int i=0;i<3;i++)
      {
         if(!OrderCalcMargin(type,GoldSymbol,vols[i],fill,margin))
         { marginOK=false;break; }
         requiredMargin+=margin;
      }
      if(!marginOK || AccountInfoDouble(ACCOUNT_MARGIN_FREE)<requiredMargin*1.1)
         continue;
      possible=true;break;
   }
   if(!possible)
   {
      Print("DK2 RISK BLOCK signal=",id," capacityGBP=",budget,
            " three-leg minimum cannot fit");
      return;
   }
   // Durable intent before first broker request prevents duplicates on restart.
   GlobalVariableSet(IDKey(id,"ATTEMPT"),(double)TimeGMT());
   GlobalVariablesFlush();
   double targets[3];targets[0]=tp1;targets[1]=tp2;targets[2]=tp3;
   bool allGood=true;
   for(int leg=1;leg<=3;leg++)
   {
      trade.SetExpertMagicNumber(LegMagic(id,leg));
      double tp=ValidTP(buy,targets[leg-1],q);
      bool sent=buy?trade.Buy(vols[leg-1],GoldSymbol,0,sl,tp,"DK2 "+IntegerToString((long)Hash(id))+"-"+IntegerToString(leg)):
                    trade.Sell(vols[leg-1],GoldSymbol,0,sl,tp,"DK2 "+IntegerToString((long)Hash(id))+"-"+IntegerToString(leg));
      ulong ticket=TicketForLeg(id,leg);
      if(!sent || !BrokerDone() || ticket==0 || !PositionSelectByTicket(ticket) ||
         PositionGetDouble(POSITION_SL)<=0 ||
         MathAbs(PositionGetDouble(POSITION_VOLUME)-vols[leg-1])>LotStep()/2)
      {
         Print("DK2 CRITICAL incomplete trio id=",id," leg=",leg,
               " retcode=",trade.ResultRetcode());
         allGood=false;break;
      }
   }
   if(!allGood)
   {
      Print("DK2 CRITICAL attempting emergency unwind of partial entry ",id);
      if(!CloseGroup(id)) Print("DK2 CRITICAL residual hedging leg requires review ",id);
   }
   else Print("DK2 trio opened id=",id," TP1=",a," TP2=",b," TP3=",c);
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
   for(int leg=1;leg<=3;leg++)
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
   for(int leg=3;leg>=1 && needed>=LotMin()-1e-8;leg--)
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
   for(int leg=1;leg<=3;leg++)
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
      int emitted=0;
      for(int i=0;i<HistoryDealsTotal() && emitted<1000;i++)
      {
         ulong deal=HistoryDealGetTicket(i);
         if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=GoldSymbol) continue;
         ulong magic=(ulong)HistoryDealGetInteger(deal,DEAL_MAGIC);
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
         json+=",\"time\":\""+IsoUTC(utcTime)+"\"}";
         emitted++;
      }
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
   if(count<1 || StringFind(lines[0],"DKDEMO2")!=0)
   {Print("DK2 ALERT invalid v2 instruction feed");return;}
   for(int i=1;i<count;i++)
   {
      string f[];StringTrimLeft(lines[i]);StringTrimRight(lines[i]);
      if(lines[i]=="") continue;
      int fields=StringSplit(lines[i],'|',f);
      if(fields==12 && f[0]=="S")
      {
         if(f[3]!="BUY" && f[3]!="SELL") continue;
         double low=StringToDouble(f[4]),high=StringToDouble(f[5]);
         if(low<=0 || high<=0) continue;
         TryEntry(f[1],f[2],f[3],low,high,StringToDouble(f[6]),
            (long)StringToInteger(f[11]),StringToDouble(f[8]),
            StringToDouble(f[9]),StringToDouble(f[10]));
         ManageLegs(f[1],f[3],StringToDouble(f[6]),f[7],
            StringToDouble(f[8]),StringToDouble(f[9]),StringToDouble(f[10]));
      }
      else if(fields==5 && f[0]=="A")
         HandleAction(f[1],StringToInteger(f[2]),f[3],StringToDouble(f[4]));
   }
}
void OnTimer()
{
   if(in_cycle) return;
   in_cycle=true;
   if(!DemoIdentity()) {in_cycle=false;return;}
   if(TimeGMT()-last_telemetry>=TelemetryEverySeconds)
   {
      SendTelemetry();last_telemetry=TimeGMT();
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
      MaxGroupRiskGBP<=0 || MaxPortfolioRiskGBP<MaxGroupRiskGBP)
   {Print("DK2 invalid configuration");return INIT_PARAMETERS_INCORRECT;}
   if(!SymbolSelect(GoldSymbol,true)) return INIT_FAILED;
   trade.SetAsyncMode(false);
   trade.SetTypeFillingBySymbol(GoldSymbol);
   trade.SetDeviationInPoints(DeviationPoints);
   EventSetTimer(PollSeconds);
   Print("DK2 DEMO Account 1 ready entryEnabled=",EnableNewEntries,
         " login=",ExpectedDemoLogin);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) {EventKillTimer();}
