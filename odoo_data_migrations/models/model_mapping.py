from itertools import product

from attr import attributes

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

from odoo.release import product_name

_logger = logging.getLogger(__name__)

class ModelMapping(models.Model):
    _name = "model.mapping"
    _description = "Model Mapping for Migration"

    model_id = fields.Char(string="Model ID")
    source_db_id = fields.Integer(string="Source Database ID")
    target_db_id = fields.Integer(string="Target Database ID")


    def move_data_from_source_many_to_many_table(self,source_table):
        """Move data from a source table to the corresponding Odoo model."""
        source_model = source_table.replace('_', '.')

        try:
            source_db, target_db = self._check_connection()

            source_records, source_columns = self._fetch_source_data(source_db, source_table)
            if source_table == 'product_attribute_value_product_template_attribute_line_rel':
                shared_fields = self._get_shared_fields_sql(source_table, source_columns)
            else:
                shared_fields = self._get_shared_fields(source_model, source_columns)

            for record in source_records:
                record_values = self._prepare_record_values(source_table, record, source_columns, shared_fields)
                print(record_values)
                self._insert_many_to_many(target_db, source_table, record_values)

        except Exception as e:
            _logger.error(f"Error during migration: {e}")
            raise ValidationError(_("Error during migration: %s") % str(e))



    def move_data_from_source_table(self, source_table):
        """Move data from a source table to the corresponding Odoo model."""
        source_model = source_table.replace('_', '.')
        try:
            source_db, target_db = self._check_connection()
            existing_mapping = self._get_existing_mapping(source_model)

            if source_table == 'account_journal':
                self._drop_account_journal_constraints(target_db)

            source_records, source_columns = self._fetch_source_data(source_db, source_table)
            if source_table == 'product_attribute_value_product_template_attribute_line_rel':
                shared_fields = self._get_shared_fields_sql(source_table,source_columns)
            else:
                shared_fields = self._get_shared_fields(source_model, source_columns)

            for record in source_records:
                source_id = record[source_columns.index('id')]
                if source_id != 5:
                    continue
                if str(source_id) in existing_mapping:
                    _logger.info(f"Record {source_id} already exists. Skipping...")
                    continue

                record_values = self._prepare_record_values(source_table, record, source_columns, shared_fields)
                if source_table in ['product_template', 'product_category','product_attribute','product_attribute_value']:
                    attribute_id, value_id = self._return_attribute_value_id(source_db,source_id)

                    return
                    new_record_id = self._insert_record_orm(source_table, record_values)
                    self._create_mapping(source_table, source_id, new_record_id)

                else:
                    new_record_id = self._insert_record(target_db, source_table, record_values)
                    self._create_mapping(source_table, source_id, new_record_id)

        except Exception as e:
            _logger.error(f"Error during migration: {e}")
            raise ValidationError(_("Error during migration: %s") % str(e))

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------

    def _check_connection(self):
        """Placeholder for checking connections."""
        # Replace with actual connection handling
        return self.env['account.connect.db'].search([], limit=1).check_connection()

    def _get_existing_mapping(self, source_model):
        """Retrieve existing mappings."""
        mappings = self.search([('model_id', '=', source_model)])
        return {str(mapping.source_db_id): mapping.target_db_id for mapping in mappings}

    def _drop_account_journal_constraints(self, target_db):
        """Drop constraints for 'account_journal'."""
        try:
            with target_db.cursor() as cursor:
                cursor.execute("""
                    ALTER TABLE account_journal DROP CONSTRAINT IF EXISTS account_journal_alias_id_fkey;
                    ALTER TABLE account_journal DROP CONSTRAINT IF EXISTS account_journal_code_company_uniq;
                """)
                target_db.commit()
            _logger.info("Dropped constraints on 'account_journal'.")
        except Exception as e:
            target_db.rollback()
            _logger.error(f"Failed to drop constraints: {e}")

    def _fetch_source_data(self, source_db, source_table):
        """Fetch records and columns from source table."""
        with source_db.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {source_table} LIMIT 0")
            columns = [desc[0] for desc in cursor.description or []]
            cursor.execute(f"SELECT * FROM {source_table}")
            records = cursor.fetchall()
        return records, columns

    def _get_shared_fields(self, source_model, source_columns):
        """Find shared fields between Odoo model and source table."""
        return [
            field for field in self.env[source_model]._fields
            if field in source_columns and field != 'id'
        ]

    def _get_shared_fields_sql(self, source_table, source_columns):
        """
        Find shared fields between a source table's columns and an Odoo model's fields.

        Args:
            source_table (str): The name of the external source table.
            source_columns (list): A list of column names to check.
        Returns:
            list: A list of shared field names between the provided columns and the Odoo model's fields.
        """
        # Execute the query
        self.env.cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (source_table,))
        shared_fields = [row[0] for row in self.env.cr.fetchall() if row[0] in source_columns ]
        return shared_fields

    def _prepare_record_values(self, source_table, record, source_columns, shared_fields):
        """Prepare values for insertion based on table logic."""
        record_values = {f: record[source_columns.index(f)] for f in shared_fields}

        if source_table == 'account_journal':
            account_mapping = self._get_existing_mapping('account.account')
            record_values['alias_id'] = None

            for account_field in ['profit_account_id', 'loss_account_id']:
                if record_values.get(account_field):
                    record_values[account_field] = int(account_mapping.get(str(record_values[account_field]), 0))

            default_debit_id = record[source_columns.index('default_debit_account_id')]
            if default_debit_id:
                record_values['default_account_id'] = int(account_mapping.get(str(default_debit_id), 0))

        elif source_table == 'account_account':
            user_type_id = record[source_columns.index('user_type_id')]
            user_type_name = self._get_account_type_name(user_type_id)
            account_type_selection = dict(
                (v, k) for k, v in self.env['account.account'].fields_get(
                    allfields=['account_type'])['account_type']['selection']
            )
            record_values['account_type'] = account_type_selection.get(user_type_name)

        elif source_table == 'product_category':
            parent_cat_mapping = self._get_existing_mapping(source_table)
            record_values['parent_id'] = parent_cat_mapping.get(str(record[source_columns.index('parent_id')]), 0)

        elif source_table == 'product_template':
            product_cat_mapping = self._get_existing_mapping('product_category')
            record_values['categ_id'] = product_cat_mapping.get(str(record[source_columns.index('categ_id')]), 0)

        elif source_table == 'product_product':
            product_template_mapping = self._get_existing_mapping('product_template')
            record_values['product_tmpl_id'] = product_template_mapping.get(str(record[source_columns.index('product_tmpl_id')]), 0)

        elif source_table == 'product_attribute_value':
            product_attribute_mapping = self._get_existing_mapping('product_attribute')
            record_values['attribute_id'] = product_attribute_mapping.get(str(record[source_columns.index('attribute_id')]), 0)

        elif source_table == 'product_template_attribute_line':
            product_template_mapping = self._get_existing_mapping('product_template')
            product_attribute_mapping = self._get_existing_mapping('product_attribute')
            record_values['product_tmpl_id'] = product_template_mapping.get(str(record[source_columns.index('product_tmpl_id')]), 0)
            record_values['attribute_id'] = product_attribute_mapping.get(str(record[source_columns.index('attribute_id')]), 0)

        elif source_table == 'product_attribute_value_product_template_attribute_line_rel':
            product_attribute_value = self._get_existing_mapping('product_attribute_value')
            product_template_attribute_line_mapping = self._get_existing_mapping('product_template_attribute_line')
            print(product_attribute_value)
            print(product_template_attribute_line_mapping)
            record_values['product_attribute_value_id'] = product_attribute_value.get(str(record[source_columns.index('product_attribute_value_id')]), 0)
            record_values['product_template_attribute_line_id'] = product_template_attribute_line_mapping.get(str(record[source_columns.index('product_template_attribute_line_id')]), 0)






        return record_values

    def _get_account_type_name(self, user_type_id):
        """Fetch account type name from user_type_id."""
        source_db, _ = self._check_connection()
        with source_db.cursor() as cursor:
            cursor.execute("SELECT name FROM account_account_type WHERE id = %s", (user_type_id,))
            result = cursor.fetchone()
        return result[0] if result else None


    def _insert_many_to_many(self, target_db, source_table, record_values):
        """Insert a new record into the many to many target table."""
        try:
            with target_db.cursor() as cursor:
                columns = ', '.join(record_values.keys())
                placeholders = ', '.join(['%s'] * len(record_values))
                query = f"INSERT INTO {source_table} ({columns}) VALUES ({placeholders})"
                cursor.execute(query, list(record_values.values()))
                target_db.commit()
        except Exception as e:
            target_db.rollback()
            _logger.error(f"Failed to insert record: {e}")
            raise ValidationError(_("Failed to insert record: %s") % str(e))

    def _insert_record(self, target_db, source_table, record_values):
        """Insert a new record into the target table."""
        try:
            with target_db.cursor() as cursor:
                columns = ', '.join(record_values.keys())
                placeholders = ', '.join(['%s'] * len(record_values))
                query = f"INSERT INTO {source_table} ({columns}) VALUES ({placeholders}) RETURNING id;"
                cursor.execute(query, list(record_values.values()))
                new_id = cursor.fetchone()[0]
                target_db.commit()
                return new_id
        except Exception as e:
            target_db.rollback()
            _logger.error(f"Failed to insert record: {e}")
            raise ValidationError(_("Failed to insert record: %s") % str(e))

    def _insert_record_orm(self, source_table, record_values):
        """Insert a new record into the target table using ORM."""
        source_table = source_table.replace('_', '.')
        model = self.env[source_table]
        try:
            if source_table == 'product.template':
                """Test skip bom line with same attribute values in bom lines."""

                Product = self.env['product.product']
                ProductAttribute = self.env['product.attribute']
                ProductAttributeValue = self.env['product.attribute.value']

                # Product Attribute
                att_color = ProductAttribute.create({'name': 'Color', 'sequence': 1})
                att_size = ProductAttribute.create({'name': 'size', 'sequence': 2})

                # Product Attribute color Value
                att_color_red = ProductAttributeValue.create(
                    {'name': 'red', 'attribute_id': att_color.id, 'sequence': 1})
                att_color_blue = ProductAttributeValue.create(
                    {'name': 'blue', 'attribute_id': att_color.id, 'sequence': 2})
                # Product Attribute size Value
                att_size_big = ProductAttributeValue.create({'name': 'big', 'attribute_id': att_size.id, 'sequence': 1})
                att_size_medium = ProductAttributeValue.create(
                    {'name': 'medium', 'attribute_id': att_size.id, 'sequence': 2})

                # Create Template Product
                product_template = self.env['product.template'].create({
                    'name': 'Sofa',
                    'attribute_line_ids': [
                        (0, 0, {
                            'attribute_id': att_color.id,
                            'value_ids': [(6, 0, [att_color_red.id, att_color_blue.id])]
                        }),
                        (0, 0, {
                            'attribute_id': att_size.id,
                            'value_ids': [(6, 0, [att_size_big.id, att_size_medium.id])]
                        })
                    ]
                })
                return 1
            else:
                new_record = model.create(record_values)
            return new_record.id
        except Exception as e:
            _logger.error(f"Failed to insert record using ORM: {e}")
            raise ValidationError(_("Failed to insert record using ORM: %s") % str(e))

    def _create_mapping(self, model_id, source_id, target_id):
        """Create a mapping record."""
        self.create({
            'model_id': model_id,
            'source_db_id': source_id,
            'target_db_id': target_id,
        })
        _logger.info(f"Mapping created for source ID {source_id} -> target ID {target_id}")


    def _return_attribute_value_id(self,source_db,template_id):
        with source_db.cursor() as cursor:
            cursor.execute(f"SELECT * FROM product_template where id={template_id}")
            record = cursor.fetchone()
            product_template_id = record[0]
            cursor.execute(f"SELECT * FROM product_template_attribute_line where product_tmpl_id={product_template_id}")
            line_ids = cursor.fetchall()
            print(line_ids)



        return 1,1

