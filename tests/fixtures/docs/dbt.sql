SELECT o.id, o.total, c.email, {{ dbt_utils.star(ref('orders')) }}
FROM {{ ref('orders') }} o
JOIN {{ ref('customers') }} c ON c.id = o.customer_id
WHERE o.status = 'complete' AND o.total > 0;
