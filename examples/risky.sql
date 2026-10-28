SELECT *
FROM orders o
JOIN customers c
WHERE LOWER(o.email) = 'analyst@example.com'
  AND o.note LIKE '%renewal'
ORDER BY RAND()
LIMIT 1000, 20;

UPDATE orders
SET state = 'archived';
