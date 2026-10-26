// DK Capital read-only MT5 identity diagnostic (does not trade).
#property strict
#property script_show_inputs
#property version "1.00"

input long ExpectedLogin = 24038172;
input string ExpectedServer = "VantageMarkets-Live14";
input string ExpectedCurrency = "GBP";
input bool ExpectReal = true;
input bool ExpectHedging = true;

string AccountModeName(const long mode)
{
   if(mode == ACCOUNT_TRADE_MODE_REAL) return "REAL";
   if(mode == ACCOUNT_TRADE_MODE_DEMO) return "DEMO";
   if(mode == ACCOUNT_TRADE_MODE_CONTEST) return "CONTEST";
   return "UNKNOWN(" + IntegerToString(mode) + ")";
}

string MarginModeName(const long mode)
{
   if(mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) return "RETAIL_HEDGING";
   if(mode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING) return "RETAIL_NETTING";
   if(mode == ACCOUNT_MARGIN_MODE_EXCHANGE) return "EXCHANGE";
   return "UNKNOWN(" + IntegerToString(mode) + ")";
}

void Report(const string field,const string actual,const string expected,const bool matches)
{
   Print("DK ACCOUNT ID PROBE ",matches ? "PASS" : "FAIL"," ",field,
         " actual=[",actual,"] expected=[",expected,"]");
}

void OnStart()
{
   const long login=AccountInfoInteger(ACCOUNT_LOGIN);
   const long trade_mode=AccountInfoInteger(ACCOUNT_TRADE_MODE);
   const long margin_mode=AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   const string currency=AccountInfoString(ACCOUNT_CURRENCY);
   const string server=AccountInfoString(ACCOUNT_SERVER);
   const long required_trade_mode=ExpectReal ? ACCOUNT_TRADE_MODE_REAL : ACCOUNT_TRADE_MODE_DEMO;
   const long required_margin_mode=ExpectHedging ? ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
                                                  : ACCOUNT_MARGIN_MODE_RETAIL_NETTING;
   int failures=0;
   Print("DK ACCOUNT ID PROBE BEGIN — read-only, no orders or EA changes");
   bool match=login==ExpectedLogin;
   Report("login",IntegerToString(login),IntegerToString(ExpectedLogin),match);
   if(!match) failures++;
   match=trade_mode==required_trade_mode;
   Report("account_type",AccountModeName(trade_mode),AccountModeName(required_trade_mode),match);
   if(!match) failures++;
   match=currency==ExpectedCurrency;
   Report("currency",currency,ExpectedCurrency,match);
   if(!match) failures++;
   match=server==ExpectedServer;
   Report("server",server,ExpectedServer,match);
   if(!match) failures++;
   match=margin_mode==required_margin_mode;
   Report("margin_mode",MarginModeName(margin_mode),MarginModeName(required_margin_mode),match);
   if(!match) failures++;
   Print("DK ACCOUNT ID PROBE END failures=",failures,
         failures==0 ? " (identity checks PASS; investigate exact EA inputs)" :
                       " (EA correctly blocks this broker-account configuration)");
}
