-- МИНИСТЕРСТВО ГЛУПОСТЕЙ V23 — выполнить целиком в Supabase > SQL Editor.
-- Совместим со старой таблицей supabase.sql из V22. Повторный запуск безопасен.
-- Таблица приватная: браузер НЕ получает ключ или прямой доступ.

CREATE TABLE IF NOT EXISTS public.ministry_scores (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player varchar(24) NOT NULL,
    country char(2) NOT NULL CHECK (country IN ('RU','US','CN','JP','IT','IL','KP')),
    days smallint NOT NULL CHECK (days BETWEEN 1 AND 20),
    score integer NOT NULL CHECK (score BETWEEN 0 AND 10000),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Миграция со старой схемы без player_key.
ALTER TABLE public.ministry_scores ADD COLUMN IF NOT EXISTS player_key text;
UPDATE public.ministry_scores
SET player_key = lower(btrim(player))
WHERE player_key IS NULL;

-- Оставляем только лучший результат каждого ника в каждой стране.
-- При равных очках приоритет большее число дней, затем более ранняя запись.
WITH duplicate_rows AS (
  SELECT id, row_number() OVER (
    PARTITION BY player_key,country
    ORDER BY score DESC,days DESC,created_at ASC,id ASC
  ) AS rank_no
  FROM public.ministry_scores
)
DELETE FROM public.ministry_scores AS scores
USING duplicate_rows AS d
WHERE scores.id=d.id AND d.rank_no>1;

ALTER TABLE public.ministry_scores ALTER COLUMN player_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ministry_scores_unique_nick_country
  ON public.ministry_scores(player_key,country);
CREATE INDEX IF NOT EXISTS ministry_scores_rank_idx
  ON public.ministry_scores(score DESC,days DESC,created_at ASC);
CREATE INDEX IF NOT EXISTS ministry_scores_country_rank_idx
  ON public.ministry_scores(country,score DESC,days DESC,created_at ASC);

ALTER TABLE public.ministry_scores ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.ministry_scores FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.ministry_scores TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.ministry_scores_id_seq TO service_role;

-- Один атомарный серверный запрос: более слабый результат не затирает рекорд.
CREATE OR REPLACE FUNCTION public.ministry_submit_score(
  p_player text, p_country text, p_days integer, p_score integer
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE changed_rows integer;
BEGIN
  IF p_player IS NULL OR length(btrim(p_player)) NOT BETWEEN 1 AND 24
     OR p_country IS NULL OR p_country NOT IN ('RU','US','CN','JP','IT','IL','KP')
     OR p_days IS NULL OR p_days NOT BETWEEN 1 AND 20
     OR p_score IS NULL OR p_score NOT BETWEEN 0 AND 10000 THEN
    RAISE EXCEPTION 'Invalid leaderboard entry';
  END IF;

  INSERT INTO public.ministry_scores (player,player_key,country,days,score)
  VALUES (btrim(p_player),lower(btrim(p_player)),p_country,p_days,p_score)
  ON CONFLICT (player_key,country) DO UPDATE
    SET player=EXCLUDED.player,
        days=EXCLUDED.days,
        score=EXCLUDED.score,
        created_at=now()
  WHERE EXCLUDED.score > ministry_scores.score
     OR (EXCLUDED.score = ministry_scores.score
         AND EXCLUDED.days > ministry_scores.days);
  GET DIAGNOSTICS changed_rows = ROW_COUNT;
  RETURN changed_rows > 0;
END;
$$;

REVOKE ALL ON FUNCTION public.ministry_submit_score(text,text,integer,integer)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ministry_submit_score(text,text,integer,integer)
TO service_role;

NOTIFY pgrst, 'reload schema';
