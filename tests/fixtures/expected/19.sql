-- #19: create function (dollar-quoted plpgsql body)
CREATE OR REPLACE FUNCTION get_customer_ltv(p_customer_id INT)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$

DECLARE v_ltv NUMERIC;

BEGIN

SELECT SUM(total) INTO v_ltv
FROM orders
WHERE customer_id = p_customer_id
  AND status      = 'complete';

IF v_ltv IS NULL
  THEN v_ltv := 0;
END IF;

RETURN v_ltv;

END;
$$;
