import os
import unittest
import json
import sqlite3
import pandas as pd
from unittest.mock import patch, MagicMock

# Override DB and Excel file names before importing backend to avoid modifying live data
import pos_backend
pos_backend.DB_FILE = "test_pos_database.db"
pos_backend.EXCEL_FILE = "test_Basededatos_Actualizada.xlsx"
# Use a fixed sync secret so tests can send it in headers
pos_backend.SYNC_SECRET = "test-sync-secret"

# Mock the sync function to avoid hitting the actual network in background sync thread
pos_backend.do_sync_to_central = lambda: (1, "Mocked sync")

class POSBackendTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create a dummy excel file for testing import
        cls.excel_data = pd.DataFrame([
            {"Codigo": "T1", "Descripcion": "Tequila Test 375ml", "Precio venta": 120.50},
            {"Codigo": "T2", "Descripcion": "Mezcal Test 750ml", "Precio venta": 250.00},
            {"Codigo": "T3", "Descripcion": "Vino Tinto Test", "Precio venta": 180.00}
        ])
        cls.excel_data.to_excel(pos_backend.EXCEL_FILE, index=False)
        
        # Configure flask app for testing
        pos_backend.app.config['TESTING'] = True
        cls.client = pos_backend.app.test_client()

    @classmethod
    def tearDownClass(cls):
        # Remove dummy Excel file
        if os.path.exists(pos_backend.EXCEL_FILE):
            os.remove(pos_backend.EXCEL_FILE)

    def setUp(self):
        # Ensure fresh database for each test
        if os.path.exists(pos_backend.DB_FILE):
            os.remove(pos_backend.DB_FILE)
        pos_backend.init_db()
        pos_backend.import_excel_data()

    def tearDown(self):
        # Clean up database after each test
        if os.path.exists(pos_backend.DB_FILE):
            try:
                os.remove(pos_backend.DB_FILE)
            except PermissionError:
                # In case file is temporarily locked by sqlite connections
                pass

    # ── Helper ────────────────────────────────────────────────────────────────

    def _get_admin_token(self):
        """Log in as admin and return the auth token."""
        res = self.client.post('/api/login', json={"username": "admin", "password": "admin123"})
        return json.loads(res.data)['token']

    def _admin_headers(self):
        return {'X-Auth-Token': self._get_admin_token()}

    def _almacen_token(self):
        res = self.client.post('/api/login', json={"username": "almacen", "password": "almacen123"})
        return json.loads(res.data)['token']

    def _almacen_headers(self):
        return {'X-Auth-Token': self._almacen_token()}

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_database_initialization(self):
        # Verify that default users exist
        conn = pos_backend.get_db()
        users = conn.execute("SELECT username, role FROM users").fetchall()
        usernames = [u['username'] for u in users]
        self.assertIn('admin', usernames)
        self.assertIn('caja', usernames)
        
        # Verify default products loaded from dummy excel
        products = conn.execute("SELECT codigo, descripcion, precio, stock FROM products").fetchall()
        self.assertEqual(len(products), 3)
        self.assertEqual(products[0]['codigo'], 'T1')
        self.assertEqual(products[0]['precio'], 120.50)
        self.assertEqual(products[0]['stock'], 0) # default stock is 0
        conn.close()

    def test_login_success(self):
        response = self.client.post('/api/login', json={
            "username": "admin",
            "password": "admin123"
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['user']['username'], 'admin')
        self.assertEqual(data['user']['role'], 'admin')
        self.assertIn('token', data)

    def test_login_failure(self):
        response = self.client.post('/api/login', json={
            "username": "admin",
            "password": "wrong_password"
        })
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        self.assertIn('Credenciales', data['message'])

    def test_get_products(self):
        # Get all products (public endpoint — no token needed)
        response = self.client.get('/api/products')
        self.assertEqual(response.status_code, 200)
        products = json.loads(response.data)
        self.assertEqual(len(products), 3)
        
        # Filter products
        response = self.client.get('/api/products?q=Mezcal')
        self.assertEqual(response.status_code, 200)
        products_filtered = json.loads(response.data)
        self.assertEqual(len(products_filtered), 1)
        self.assertEqual(products_filtered[0]['codigo'], 'T2')

    def test_update_stock(self):
        # Update T1 stock — requires admin/almacen token
        response = self.client.put('/api/products/T1/stock',
                                   json={"stock": 15},
                                   headers=self._admin_headers())
        self.assertEqual(response.status_code, 200)
        
        # Check database
        conn = pos_backend.get_db()
        product = conn.execute("SELECT stock FROM products WHERE codigo = 'T1'").fetchone()
        self.assertEqual(product['stock'], 15)
        conn.close()

    def test_update_stock_requires_auth(self):
        response = self.client.put('/api/products/T1/stock', json={"stock": 15})
        self.assertEqual(response.status_code, 401)

    def test_update_product_details(self):
        response = self.client.put('/api/products/T1',
                                   json={"descripcion": "Tequila Modificado", "precio": 130.00, "stock": 20},
                                   headers=self._admin_headers())
        self.assertEqual(response.status_code, 200)
        
        conn = pos_backend.get_db()
        product = conn.execute("SELECT descripcion, precio, stock FROM products WHERE codigo = 'T1'").fetchone()
        self.assertEqual(product['descripcion'], "Tequila Modificado")
        self.assertEqual(product['precio'], 130.00)
        self.assertEqual(product['stock'], 20)
        conn.close()

    def test_register_sale_valid_cash(self):
        # Pre-populate stock
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=self._admin_headers())
        
        # Make a sale
        response = self.client.post('/api/sales', json={
            "user_id": 2, # 'caja' user has id 2 usually, let's verify or fetch it
            "items": [
                {"codigo": "T1", "precio": 120.50, "quantity": 2}
            ],
            "payment_method": "efectivo",
            "discount": 10.00,
            "vendor": "Ismael Perez"
        })
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['subtotal'], 241.00)
        self.assertEqual(data['discount'], 10.00)
        self.assertEqual(data['total'], 231.00)
        self.assertEqual(data['cash_amount'], 231.00)
        self.assertEqual(data['card_amount'], 0)
        
        # Verify stock decremented
        conn = pos_backend.get_db()
        product = conn.execute("SELECT stock FROM products WHERE codigo = 'T1'").fetchone()
        self.assertEqual(product['stock'], 3) # 5 - 2 = 3
        
        # Verify sale recorded
        sale = conn.execute("SELECT * FROM sales WHERE id = ?", (data['sale_id'],)).fetchone()
        self.assertIsNotNone(sale)
        self.assertEqual(sale['total'], 231.00)
        self.assertEqual(sale['discount'], 10.00)
        self.assertEqual(sale['vendor'], "Ismael Perez")
        conn.close()

    def test_register_sale_valid_mixed(self):
        self.client.put('/api/products/T1/stock', json={"stock": 10}, headers=self._admin_headers())
        
        # Total = 120.50 * 2 = 241.00. Discount = 20. Total = 221.00
        # Mixed: 100 cash, 121 card. Total payment = 221.00
        response = self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [
                {"codigo": "T1", "precio": 120.50, "quantity": 2}
            ],
            "payment_method": "mixto",
            "discount": 20.00,
            "cash_amount": 100.00,
            "card_amount": 121.00,
            "vendor": "Silvano Lopez"
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['cash_amount'], 100.00)
        self.assertEqual(data['card_amount'], 121.00)

    def test_register_sale_invalid_mixed_mismatch(self):
        self.client.put('/api/products/T1/stock', json={"stock": 10}, headers=self._admin_headers())
        
        # Total = 241.00. Cash = 100, Card = 100. Sum = 200. Difference = 41 > 1.00 tolerance.
        response = self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [
                {"codigo": "T1", "precio": 120.50, "quantity": 2}
            ],
            "payment_method": "mixto",
            "discount": 0,
            "cash_amount": 100.00,
            "card_amount": 100.00
        })
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertFalse(data['success'])
        self.assertIn("suma del pago mixto debe ser igual al total", data['message'])

    def test_register_sale_degustacion(self):
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=self._admin_headers())
        
        # Degustación occurs when discount = subtotal
        response = self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [
                {"codigo": "T1", "precio": 120.50, "quantity": 2}
            ],
            "payment_method": "efectivo",
            "discount": 241.00
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['total'], 0)
        
        # Check that user_id was recorded as NULL in the database
        conn = pos_backend.get_db()
        sale = conn.execute("SELECT user_id FROM sales WHERE id = ?", (data['sale_id'],)).fetchone()
        self.assertIsNone(sale['user_id'])
        conn.close()

    def test_register_sale_invalid_discount(self):
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=self._admin_headers())
        
        # Negative discount
        response = self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [{"codigo": "T1", "precio": 120.50, "quantity": 1}],
            "discount": -5.00
        })
        self.assertEqual(response.status_code, 400)
        
        # Discount > subtotal
        response = self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [{"codigo": "T1", "precio": 120.50, "quantity": 1}],
            "discount": 150.00
        })
        self.assertEqual(response.status_code, 400)

    def test_get_sale_items_and_receipt(self):
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=self._admin_headers())
        
        sale_res = self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [{"codigo": "T1", "precio": 120.50, "quantity": 1}],
            "payment_method": "efectivo",
            "discount": 0.0
        })
        sale_id = json.loads(sale_res.data)['sale_id']
        
        # Get items
        items_res = self.client.get(f'/api/sales/{sale_id}/items')
        self.assertEqual(items_res.status_code, 200)
        items = json.loads(items_res.data)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['product_codigo'], 'T1')
        self.assertEqual(items[0]['quantity'], 1)
        
        # Get receipt
        receipt_res = self.client.get(f'/api/receipt/{sale_id}')
        self.assertEqual(receipt_res.status_code, 200)
        receipt_data = json.loads(receipt_res.data)
        self.assertTrue(receipt_data['success'])
        self.assertEqual(receipt_data['receipt']['sale_id'], sale_id)
        self.assertEqual(len(receipt_data['receipt']['items']), 1)

    def test_exchange_rate_endpoints(self):
        token = self._get_admin_token()
        # Get default rate (public)
        get_res = self.client.get('/api/exchange-rate')
        self.assertEqual(get_res.status_code, 200)
        data = json.loads(get_res.data)
        self.assertEqual(data['exchange_rate'], 17.5)
        
        # Update rate — requires admin token
        post_res = self.client.post('/api/admin/exchange-rate',
                                    json={"exchange_rate": 18.25},
                                    headers={'X-Auth-Token': token})
        self.assertEqual(post_res.status_code, 200)
        
        # Get updated rate
        get_res = self.client.get('/api/exchange-rate')
        self.assertEqual(json.loads(get_res.data)['exchange_rate'], 18.25)
        
        # Invalid exchange rate
        invalid_res = self.client.post('/api/admin/exchange-rate',
                                       json={"exchange_rate": -1.0},
                                       headers={'X-Auth-Token': token})
        self.assertEqual(invalid_res.status_code, 400)

    def test_exchange_rate_requires_auth(self):
        response = self.client.post('/api/admin/exchange-rate', json={"exchange_rate": 20.0})
        self.assertEqual(response.status_code, 401)

    def test_admin_income_and_inventory(self):
        headers = self._admin_headers()
        # Update stock of T1
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=headers)
        # Record a sale
        self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [{"codigo": "T1", "precio": 120.00, "quantity": 1}],
            "payment_method": "efectivo",
            "discount": 20.00
        })
        
        # Get income stats — requires admin token
        income_res = self.client.get('/api/admin/income', headers=headers)
        self.assertEqual(income_res.status_code, 200)
        income_data = json.loads(income_res.data)
        self.assertEqual(income_data['total_sales_count'], 1)
        self.assertEqual(income_data['all_time_total'], 100.00) # 120 - 20 = 100
        
        # Get inventory stats — requires admin/almacen token
        inventory_res = self.client.get('/api/admin/inventory?sort=stock&order=desc', headers=headers)
        self.assertEqual(inventory_res.status_code, 200)
        inventory_data = json.loads(inventory_res.data)
        self.assertEqual(len(inventory_data), 3)

    def test_sync_endpoints(self):
        headers = self._admin_headers()
        # Add unsynced sale
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=headers)
        self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [{"codigo": "T1", "precio": 120.00, "quantity": 1}],
            "payment_method": "efectivo",
            "discount": 0
        })
        
        # Test mock sync call — requires admin token
        sync_res = self.client.post('/api/sync', headers=headers)
        self.assertEqual(sync_res.status_code, 200)
        self.assertTrue(json.loads(sync_res.data)['success'])
        
        # Test remote_sync endpoint — requires X-Sync-Secret
        remote_res = self.client.post('/api/remote_sync',
                                      json={
                                          "sales": [
                                              {
                                                  "user_id": 2,
                                                  "subtotal": 120.00,
                                                  "discount": 0,
                                                  "total": 120.00,
                                                  "payment_method": "efectivo",
                                                  "cash_amount": 120.00,
                                                  "card_amount": 0,
                                                  "timestamp": "2026-06-23T12:00:00",
                                                  "store": "Sucursal Norte",
                                                  "source_store": "Sucursal Norte",
                                                  "source_sale_id": 99,
                                                  "items": [
                                                      {"product_codigo": "T1", "quantity": 1, "subtotal": 120.00}
                                                  ]
                                              }
                                          ]
                                      },
                                      headers={'X-Sync-Secret': 'test-sync-secret'})
        self.assertEqual(remote_res.status_code, 200)
        self.assertTrue(json.loads(remote_res.data)['success'])

        # Verify remote_sync does NOT decrement stock on the central server
        conn = pos_backend.get_db()
        product = conn.execute("SELECT stock FROM products WHERE codigo = 'T1'").fetchone()
        self.assertEqual(product['stock'], 4)  # only the local sale decremented it (5 - 1 = 4)

        # Check sales count increased
        sales_count = conn.execute("SELECT COUNT(*) as count FROM sales").fetchone()['count']
        self.assertEqual(sales_count, 2)

        # Check origin tracing columns were stored
        synced_sale = conn.execute(
            "SELECT source_store, source_sale_id FROM sales WHERE source_store IS NOT NULL"
        ).fetchone()
        self.assertIsNotNone(synced_sale)
        self.assertEqual(synced_sale['source_store'], 'Sucursal Norte')
        self.assertEqual(synced_sale['source_sale_id'], 99)
        conn.close()

    def test_remote_sync_requires_secret(self):
        response = self.client.post('/api/remote_sync', json={"sales": []})
        self.assertEqual(response.status_code, 401)

    @patch('subprocess.run')
    def test_excel_export(self, mock_subprocess_run):
        headers = self._admin_headers()
        # Register a sale first to have data to export
        self.client.put('/api/products/T1/stock', json={"stock": 5}, headers=headers)
        self.client.post('/api/sales', json={
            "user_id": 2,
            "items": [{"codigo": "T1", "precio": 120.00, "quantity": 1}],
            "payment_method": "efectivo",
            "discount": 10.00
        })
        
        # Call export endpoint — requires admin token
        response = self.client.get('/api/admin/export', headers=headers)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertIn("Guardado en Exportaciones", data['message'])
        
        # Check that mock_subprocess_run was called or not based on os.name
        if os.name == 'nt':
            self.assertTrue(mock_subprocess_run.called)
            
        # Clean up export directory files created during tests
        export_dir = os.path.join(pos_backend.get_data_dir(), 'Exportaciones')
        if os.path.exists(export_dir):
            for file in os.listdir(export_dir):
                if file.startswith("Ventas_NXT_POS_") and file.endswith(".xlsx"):
                    try:
                        os.remove(os.path.join(export_dir, file))
                    except Exception:
                        pass

if __name__ == '__main__':
    unittest.main()
