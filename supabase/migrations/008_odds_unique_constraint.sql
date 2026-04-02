-- 008: Add unique constraint on (game_pk, book) for upsert support
ALTER TABLE odds ADD CONSTRAINT odds_game_pk_book_unique UNIQUE (game_pk, book);
