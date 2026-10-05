// EURGBP tick export for the Relative Value Engine.
// Attach this Expert Advisor to an EURGBP chart. It overwrites one JSON object
// in Terminal Common Files. The Python process only reads that file.
// Orders are not sent from this file.

#property copyright "Relative Value Engine"
#property version   "1.00"
#property strict

input string OutFile = "RelativeValueEngine\\eurgbp_tick.json";

void OnTick()
{
   if(Symbol() != "EURGBP")
      return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   if(tick.bid <= 0.0 || tick.ask <= 0.0 || tick.bid > tick.ask)
      return;
   string payload = StringFormat(
      "{\"symbol\":\"EURGBP\",\"time\":%I64d,\"bid\":%.5f,\"ask\":%.5f}",
      (long)TimeGMT(),
      tick.bid,
      tick.ask
   );
   int handle = FileOpen(OutFile, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return;
   FileWriteString(handle, payload);
   FileClose(handle);
}
