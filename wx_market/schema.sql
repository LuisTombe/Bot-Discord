-- Additive schema; existing eco_* wallet, payroll and mission tables remain authoritative.
CREATE TABLE IF NOT EXISTS market_versions(version INTEGER PRIMARY KEY, applied INTEGER NOT NULL);
INSERT OR IGNORE INTO market_versions VALUES(1,unixepoch());
CREATE TABLE IF NOT EXISTS market_items(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, search_name TEXT NOT NULL, category TEXT NOT NULL,
 type TEXT NOT NULL, rank TEXT NOT NULL, price INTEGER NOT NULL CHECK(price>=0),
 price_x3 INTEGER NOT NULL CHECK(price_x3=price*3), enabled INTEGER NOT NULL,
 digest TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_item_filters ON market_items(enabled,category,type,rank,price);
CREATE INDEX IF NOT EXISTS market_item_price ON market_items(enabled,price);
CREATE INDEX IF NOT EXISTS market_item_name ON market_items(search_name);
CREATE TABLE IF NOT EXISTS market_tags(item TEXT NOT NULL REFERENCES market_items(id),kind TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(item,kind,value));
CREATE INDEX IF NOT EXISTS market_tag_filter ON market_tags(kind,value,item);
CREATE TABLE IF NOT EXISTS market_profiles(guild INTEGER NOT NULL,user INTEGER NOT NULL,data TEXT NOT NULL,verified_by INTEGER NOT NULL,verified_at INTEGER NOT NULL,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS market_requests(
 id INTEGER PRIMARY KEY, guild INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('purchase','trade','teaching','loan')),
 actor INTEGER NOT NULL, target INTEGER NOT NULL, item TEXT, lot INTEGER, price INTEGER NOT NULL CHECK(price>=0),
 state TEXT NOT NULL CHECK(state IN ('offered','pending','approved','rejected','cancelled','expired')),
 snapshot TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL, expires INTEGER NOT NULL,
 reviewed_by INTEGER, reviewed_at INTEGER, reason TEXT, tx INTEGER, review_channel INTEGER, review_message INTEGER,
 dirty INTEGER NOT NULL DEFAULT 1, reference TEXT NOT NULL, UNIQUE(guild,reference));
CREATE INDEX IF NOT EXISTS market_request_pending ON market_requests(state,expires);
CREATE INDEX IF NOT EXISTS market_request_user ON market_requests(guild,actor,target,id);
CREATE TABLE IF NOT EXISTS market_holds(request INTEGER NOT NULL REFERENCES market_requests(id),guild INTEGER NOT NULL,user INTEGER NOT NULL,amount INTEGER NOT NULL CHECK(amount>=0),PRIMARY KEY(request,user));
CREATE INDEX IF NOT EXISTS market_hold_user ON market_holds(guild,user);
CREATE TABLE IF NOT EXISTS market_inventory(
 id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,user INTEGER NOT NULL,item TEXT NOT NULL,category TEXT NOT NULL,
 snapshot TEXT NOT NULL,origin TEXT NOT NULL,request INTEGER NOT NULL REFERENCES market_requests(id),approved_at INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'owned' CHECK(state IN ('owned','consumed','revoked')),locked_by INTEGER REFERENCES market_requests(id));
CREATE INDEX IF NOT EXISTS market_inventory_user ON market_inventory(guild,user,state,id);
CREATE TABLE IF NOT EXISTS market_loans(
 request INTEGER PRIMARY KEY REFERENCES market_requests(id),guild INTEGER NOT NULL,lender INTEGER NOT NULL,borrower INTEGER NOT NULL,
 principal INTEGER NOT NULL CHECK(principal>0),repaid INTEGER NOT NULL DEFAULT 0 CHECK(repaid>=0 AND repaid<=principal),due INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS market_loan_users ON market_loans(guild,borrower,lender,due);
CREATE TABLE IF NOT EXISTS market_audit(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,actor INTEGER NOT NULL,action TEXT NOT NULL,request INTEGER,detail TEXT NOT NULL,created INTEGER NOT NULL,posted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS market_audit_page ON market_audit(guild,id);
CREATE INDEX IF NOT EXISTS market_audit_outbox ON market_audit(posted,id);
CREATE TRIGGER IF NOT EXISTS market_audit_no_delete BEFORE DELETE ON market_audit BEGIN SELECT RAISE(ABORT,'audit append-only'); END;
CREATE TRIGGER IF NOT EXISTS market_audit_no_edit BEFORE UPDATE OF guild,actor,action,request,detail,created ON market_audit BEGIN SELECT RAISE(ABORT,'audit append-only'); END;
CREATE INDEX IF NOT EXISTS market_entry_account ON eco_entries(account,tx);
