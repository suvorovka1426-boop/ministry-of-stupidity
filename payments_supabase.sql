-- Execute in Supabase SQL Editor after existing leaderboard supabase.sql.
-- Private server-side payment tables; NEVER expose the service_role / sb_secret key.
-- All RPC functions are callable by service_role only. Player token hashes stay server-side.
CREATE TABLE IF NOT EXISTS public.ministry_pay_players (
  id text PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),
  token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.ministry_pay_orders (
  id text PRIMARY KEY CHECK (id ~ '^ms[a-f0-9]{30}$'),
  player_id text NOT NULL REFERENCES public.ministry_pay_players(id),
  operation_id text UNIQUE,
  amount_cents integer NOT NULL DEFAULT 1000 CHECK (amount_cents=1000),
  currency text NOT NULL DEFAULT 'rub' CHECK (currency='rub'),
  item_id text NOT NULL DEFAULT 'coins_120' CHECK (item_id='coins_120'),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid')),
  created_at timestamptz NOT NULL DEFAULT now(),
  fulfilled_at timestamptz,
  redeemed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ministry_pay_orders_player_idx ON public.ministry_pay_orders(player_id, created_at DESC);
CREATE TABLE IF NOT EXISTS public.ministry_pay_wallets (
  player_id text PRIMARY KEY REFERENCES public.ministry_pay_players(id),
  balance integer NOT NULL DEFAULT 0 CHECK (balance>=0)
);
CREATE TABLE IF NOT EXISTS public.ministry_pay_entitlements (
  player_id text NOT NULL REFERENCES public.ministry_pay_players(id),
  item_id text NOT NULL,
  unlocked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(player_id, item_id)
);
ALTER TABLE public.ministry_pay_players ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ministry_pay_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ministry_pay_wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ministry_pay_entitlements ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ministry_pay_players, public.ministry_pay_orders, public.ministry_pay_wallets, public.ministry_pay_entitlements FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.ministry_pay_players, public.ministry_pay_orders, public.ministry_pay_wallets, public.ministry_pay_entitlements TO service_role;

-- Rate-limit outstanding checkout orders per player, enforcing a fixed server-side price.
CREATE OR REPLACE FUNCTION public.ministry_pay_create_order(p_player text, p_order text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF p_order !~ '^ms[a-f0-9]{30}$' THEN RETURN false; END IF;
  IF (SELECT count(*) FROM public.ministry_pay_orders
      WHERE player_id=p_player AND status='pending' AND created_at>now()-interval '1 hour') >= 5
  THEN RETURN false; END IF;
  INSERT INTO public.ministry_pay_orders(id,player_id) VALUES (p_order,p_player);
  RETURN true;
END;$$;

-- Confirm ONLY after server independently verifies signed YooMoney webhook.
CREATE OR REPLACE FUNCTION public.ministry_pay_confirm(p_order text, p_operation text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_status text; v_op text;
BEGIN
  IF p_operation !~ '^[0-9]{1,40}$' THEN RETURN 'invalid'; END IF;
  SELECT status,operation_id INTO v_status,v_op
  FROM public.ministry_pay_orders WHERE id=p_order FOR UPDATE;
  IF NOT FOUND THEN RETURN 'missing'; END IF;
  IF v_status='paid' THEN
    IF v_op=p_operation THEN RETURN 'duplicate'; END IF;
    RETURN 'conflict';
  END IF;
  IF EXISTS (SELECT 1 FROM public.ministry_pay_orders WHERE operation_id=p_operation) THEN
    RETURN 'conflict';
  END IF;
  UPDATE public.ministry_pay_orders
    SET status='paid', operation_id=p_operation, fulfilled_at=now(), redeemed_at=now()
    WHERE id=p_order AND status='pending';
  -- The same transaction both confirms the transfer and credits exactly 120 coins.
  INSERT INTO public.ministry_pay_wallets(player_id,balance)
    SELECT player_id,120 FROM public.ministry_pay_orders WHERE id=p_order
    ON CONFLICT(player_id) DO UPDATE SET balance=public.ministry_pay_wallets.balance+120;
  RETURN 'paid';
END;$$;

-- Redeem a paid purchase exactly once. Lock order row so concurrent redemptions cannot duplicate coins.
-- Legacy compatibility: client-side redeem no longer awards credits.
CREATE OR REPLACE FUNCTION public.ministry_pay_redeem(p_player text)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
SELECT false;
$$;

-- Purchases with paid credits require the FULL price. Browsers cannot buy full item for one paid coin.
CREATE OR REPLACE FUNCTION public.ministry_pay_spend(p_player text,p_item text,p_amount integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_price integer; v_balance integer;
BEGIN
  IF p_item='reroll' THEN
    IF p_amount IS DISTINCT FROM 5 THEN RETURN '{"ok":false}'::jsonb; END IF;
    UPDATE public.ministry_pay_wallets SET balance=balance-5
      WHERE player_id=p_player AND balance>=5 RETURNING balance INTO v_balance;
    IF NOT FOUND THEN RETURN '{"ok":false}'::jsonb; END IF;
    RETURN jsonb_build_object('ok',true,'coins_balance',v_balance,'consumed','reroll');
  END IF;
  v_price := CASE p_item
    WHEN 'country:JP' THEN 75 WHEN 'country:IL' THEN 90
    WHEN 'country:IT' THEN 105 WHEN 'country:KP' THEN 120
    WHEN 'skin:noir' THEN 35 WHEN 'skin:robot' THEN 60
    WHEN 'skin:royalty' THEN 90 WHEN 'skin:cosmic' THEN 115
    WHEN 'theme:rose' THEN 35 WHEN 'theme:mint' THEN 55
    WHEN 'theme:royal' THEN 85 ELSE NULL END;
  IF v_price IS NULL OR p_amount IS DISTINCT FROM v_price THEN RETURN '{"ok":false}'::jsonb; END IF;
  IF EXISTS (SELECT 1 FROM public.ministry_pay_entitlements WHERE player_id=p_player AND item_id=p_item) THEN
     RETURN '{"ok":false}'::jsonb;
  END IF;
  UPDATE public.ministry_pay_wallets
     SET balance=balance-v_price
     WHERE player_id=p_player AND balance >= v_price
     RETURNING balance INTO v_balance;
  IF NOT FOUND THEN RETURN '{"ok":false}'::jsonb; END IF;
  INSERT INTO public.ministry_pay_entitlements(player_id,item_id) VALUES(p_player,p_item);
  RETURN jsonb_build_object('ok',true,'coins_balance',v_balance,'unlocked',p_item);
END;$$;

CREATE OR REPLACE FUNCTION public.ministry_pay_inventory(p_player text)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
SELECT jsonb_build_object(
 'item','coins_120', 'item_name','Набор 120 игровых монет',
 'quantity',0,
 'purchased',(SELECT count(*) FROM public.ministry_pay_orders WHERE player_id=p_player AND status='paid'),
 'used',(SELECT count(*) FROM public.ministry_pay_orders WHERE player_id=p_player AND status='paid'),
 'history',COALESCE((SELECT jsonb_agg(to_jsonb(x)) FROM
      (SELECT id,status,amount_cents,currency,EXTRACT(EPOCH FROM created_at)::bigint AS created_at
       FROM public.ministry_pay_orders WHERE player_id=p_player ORDER BY created_at DESC LIMIT 10) AS x),'[]'::jsonb),
 'coins_balance',COALESCE((SELECT balance FROM public.ministry_pay_wallets WHERE player_id=p_player),0),
 'entitlements',COALESCE((SELECT jsonb_agg(item_id) FROM public.ministry_pay_entitlements WHERE player_id=p_player),'[]'::jsonb)
);
$$;

REVOKE ALL ON FUNCTION public.ministry_pay_create_order(text,text),
  public.ministry_pay_confirm(text,text), public.ministry_pay_redeem(text),
  public.ministry_pay_spend(text,text,integer), public.ministry_pay_inventory(text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ministry_pay_create_order(text,text),
  public.ministry_pay_confirm(text,text), public.ministry_pay_redeem(text),
  public.ministry_pay_spend(text,text,integer), public.ministry_pay_inventory(text)
  TO service_role;
NOTIFY pgrst, 'reload schema';
