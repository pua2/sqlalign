create or replace function get_customer_ltv(p_customer_id int) returns numeric
language plpgsql as $$
declare v_ltv numeric;
begin
select sum(total) into v_ltv from orders
where customer_id = p_customer_id and status = 'complete';
return v_ltv;
end;
$$;
