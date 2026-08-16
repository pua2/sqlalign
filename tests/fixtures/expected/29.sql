-- #29: tsql batches separated by GO
TRUNCATE TABLE staging;
GO
INSERT INTO staging
(  a
 , b)
SELECT a
     , b
FROM source
WHERE a IS NOT NULL;
GO
