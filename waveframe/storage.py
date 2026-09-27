from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd
from .logging import AuditLogger

class MarketStore:
    def __init__(self, root="."):
        self.root=Path(root).resolve(); self.logger=AuditLogger(self.root)
        self.path=self.root/"data"/"market.sqlite3"; self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS bars(symbol TEXT,timeframe TEXT,open_time TEXT PRIMARY KEY,close_time TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL)""")
    def upsert_df(self, df:pd.DataFrame, symbol:str, timeframe:str):
        with sqlite3.connect(self.path) as c:
            for _,r in df.iterrows():
                c.execute("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?)",(symbol,timeframe,str(r.open_time),str(r.close_time),float(r.open),float(r.high),float(r.low),float(r.close),float(r.get('volume',0))))
        if len(df):
            self.logger.event("market","BARS_UPSERTED",{"symbol":symbol,"timeframe":timeframe,"count":len(df),"first_open_time":str(df.iloc[0]["open_time"]),"last_close_time":str(df.iloc[-1]["close_time"])})
    def load(self,symbol:str,timeframe:str,limit:int=500)->pd.DataFrame:
        with sqlite3.connect(self.path) as c:
            return pd.read_sql_query("SELECT * FROM bars WHERE symbol=? AND timeframe=? ORDER BY open_time DESC LIMIT ?",c,params=(symbol,timeframe,limit)).sort_values("open_time").reset_index(drop=True)
