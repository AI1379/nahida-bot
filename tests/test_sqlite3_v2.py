#
# Tests for SQLite3DBv2
#

import pytest
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from nahida_bot.localstore.sqlite3_v2 import SQLite3DBv2


# Test Models
class User(BaseModel):
    id: Optional[int] = None
    name: str
    email: str
    age: int


class Product(BaseModel):
    id: Optional[int] = None
    name: str = Field(min_length=1)
    price: float = Field(gt=0)
    description: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)


class TestSQLite3DBv2Basic:
    """Basic CRUD operations"""

    @pytest.fixture
    def db(self):
        """Create temporary database for testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            database = SQLite3DBv2(User, f"{tmpdir}/test.db", "test_users")
            yield database
            database.close()

    def test_insert_single(self, db):
        """Test inserting a single record"""
        user = User(name="Alice", email="alice@example.com", age=30)
        row_id = db.insert(user)
        assert row_id is not None
        assert row_id > 0

    def test_insert_many(self, db):
        """Test inserting multiple records"""
        users = [
            User(name="Alice", email="alice@example.com", age=30),
            User(name="Bob", email="bob@example.com", age=25),
        ]
        row_ids = db.insert_many(users)
        assert len(row_ids) == 2
        assert all(rid > 0 for rid in row_ids)

    def test_get_by_id(self, db):
        """Test retrieving record by ID"""
        user = User(name="Alice", email="alice@example.com", age=30)
        row_id = db.insert(user)

        retrieved = db.get(row_id)
        assert retrieved is not None
        assert retrieved.name == "Alice"
        assert retrieved.email == "alice@example.com"
        assert retrieved.age == 30

    def test_get_nonexistent(self, db):
        """Test retrieving nonexistent record"""
        retrieved = db.get(99999)
        assert retrieved is None

    def test_update(self, db):
        """Test updating a record"""
        user = User(name="Alice", email="alice@example.com", age=30)
        row_id = db.insert(user)

        updated = User(name="Alice", email="newemail@example.com", age=31)
        success = db.update(row_id, updated)
        assert success is True

        retrieved = db.get(row_id)
        assert retrieved.email == "newemail@example.com"
        assert retrieved.age == 31

    def test_delete_by_id(self, db):
        """Test deleting record by ID"""
        user = User(name="Alice", email="alice@example.com", age=30)
        row_id = db.insert(user)

        success = db.delete(row_id)
        assert success is True

        retrieved = db.get(row_id)
        assert retrieved is None

    def test_delete_nonexistent(self, db):
        """Test deleting nonexistent record"""
        success = db.delete(99999)
        assert success is False


class TestSQLite3DBv2Filtering:
    """Test filtering and querying"""

    @pytest.fixture
    def db(self):
        """Create temporary database with sample data"""
        with tempfile.TemporaryDirectory() as tmpdir:
            database = SQLite3DBv2(User, f"{tmpdir}/test.db", "test_users")
            # Insert sample data
            database.insert(User(name="Alice", email="alice@example.com", age=30))
            database.insert(User(name="Bob", email="bob@example.com", age=25))
            database.insert(User(name="Charlie", email="charlie@example.com", age=35))
            yield database
            database.close()

    def test_all(self, db):
        """Test getting all records"""
        users = db.all()
        assert len(users) == 3

    def test_filter_where_single(self, db):
        """Test filtering with single condition"""
        users = db.filter_where(name="Alice")
        assert len(users) == 1
        assert users[0].name == "Alice"

    def test_filter_where_multiple(self, db):
        """Test filtering with multiple conditions"""
        users = db.filter_where(name="Alice", age=30)
        assert len(users) == 1
        assert users[0].name == "Alice"
        assert users[0].age == 30

    def test_filter_where_no_match(self, db):
        """Test filtering with no results"""
        users = db.filter_where(name="NonExistent")
        assert len(users) == 0

    def test_filter_like(self, db):
        """Test LIKE pattern matching"""
        users = db.filter_like("email", "%example.com")
        assert len(users) == 3

        users = db.filter_like("name", "A%")
        assert len(users) == 1
        assert users[0].name == "Alice"

    def test_get_where(self, db):
        """Test getting first match"""
        user = db.get_where(name="Alice")
        assert user is not None
        assert user.name == "Alice"

    def test_get_where_no_match(self, db):
        """Test get_where with no results"""
        user = db.get_where(name="NonExistent")
        assert user is None

    def test_count_all(self, db):
        """Test counting all records"""
        count = db.count()
        assert count == 3

    def test_count_with_condition(self, db):
        """Test counting with condition"""
        count = db.count(age=30)
        assert count == 1

    def test_exists_true(self, db):
        """Test existence check - record exists"""
        users = db.all()
        assert db.exists(users[0].id) is True

    def test_exists_false(self, db):
        """Test existence check - record doesn't exist"""
        assert db.exists(99999) is False


class TestSQLite3DBv2Deletion:
    """Test deletion operations"""

    @pytest.fixture
    def db(self):
        """Create temporary database with sample data"""
        with tempfile.TemporaryDirectory() as tmpdir:
            database = SQLite3DBv2(User, f"{tmpdir}/test.db", "test_users")
            database.insert(User(name="Alice", email="alice@example.com", age=30))
            database.insert(User(name="Bob", email="bob@example.com", age=25))
            database.insert(User(name="Charlie", email="charlie@example.com", age=30))
            yield database
            database.close()

    def test_delete_where(self, db):
        """Test deleting with conditions"""
        count_before = db.count()
        deleted = db.delete_where(age=30)
        assert deleted == 2
        assert db.count() == count_before - 2

    def test_clear(self, db):
        """Test clearing all records"""
        db.clear()
        assert db.count() == 0

    def test_drop_and_reset(self, db):
        """Test dropping and resetting table"""
        db.drop()
        # After drop, table should be gone

        db.reset()
        # After reset, table should exist but be empty
        assert db.count() == 0


class TestSQLite3DBv2Types:
    """Test type handling"""

    @pytest.fixture
    def db(self):
        """Create temporary database for type testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            database = SQLite3DBv2(Product, f"{tmpdir}/test.db", "test_products")
            yield database
            database.close()

    def test_bool_type(self, db):
        """Test boolean field handling"""
        product = Product(name="Item", price=10.0, is_active=True)
        row_id = db.insert(product)

        retrieved = db.get(row_id)
        assert retrieved.is_active is True

        # Update to False
        product.is_active = False
        db.update(row_id, product)
        retrieved = db.get(row_id)
        assert retrieved.is_active is False

    def test_float_type(self, db):
        """Test float field handling"""
        product = Product(name="Item", price=19.99)
        row_id = db.insert(product)

        retrieved = db.get(row_id)
        assert retrieved.price == 19.99

    def test_optional_field(self, db):
        """Test optional field handling"""
        product = Product(name="Item", price=10.0, description=None)
        row_id = db.insert(product)

        retrieved = db.get(row_id)
        assert retrieved.description is None

        # Update with value
        product.description = "A useful item"
        db.update(row_id, product)
        retrieved = db.get(row_id)
        assert retrieved.description == "A useful item"

    def test_datetime_type(self, db):
        """Test datetime field handling"""
        now = datetime.now()
        product = Product(name="Item", price=10.0, created_at=now)
        row_id = db.insert(product)

        retrieved = db.get(row_id)
        # Compare with a small tolerance for datetime precision
        assert abs((retrieved.created_at - now).total_seconds()) < 1


class TestSQLite3DBv2ContextManager:
    """Test context manager usage"""

    def test_context_manager(self):
        """Test using as context manager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with SQLite3DBv2(User, f"{tmpdir}/test.db", "test_users") as db:
                user = User(name="Alice", email="alice@example.com", age=30)
                row_id = db.insert(user)
                assert row_id is not None
            # Connection should be closed


class TestSQLite3DBv2TableSchemaGeneration:
    """Test automatic table schema generation"""

    def test_schema_from_pydantic(self):
        """Test schema generation from Pydantic model"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = SQLite3DBv2(User, f"{tmpdir}/test.db", "test_users")

            # Check that schema was properly created
            assert db.schema.table_name == "test_users"
            assert "id" in db.schema.columns
            assert "name" in db.schema.columns
            assert "email" in db.schema.columns
            assert "age" in db.schema.columns
            assert db.schema.primary_key == "id"

            db.close()


class TestSQLite3DBv2PydanticValidation:
    """Test Pydantic validation integration"""

    @pytest.fixture
    def db(self):
        """Create temporary database for validation testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            database = SQLite3DBv2(Product, f"{tmpdir}/test.db", "test_products")
            yield database
            database.close()

    def test_pydantic_validation_on_model_creation(self):
        """Test that Pydantic validates before insertion"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            Product(name="", price=10.0)  # Name too short

    def test_pydantic_validation_price(self):
        """Test price validation"""
        with pytest.raises(Exception):
            Product(name="Item", price=-5.0)  # Negative price

    def test_valid_product(self, db):
        """Test valid product creation and insertion"""
        product = Product(name="Item", price=10.0)
        row_id = db.insert(product)
        assert row_id > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
