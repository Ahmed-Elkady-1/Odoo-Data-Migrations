import logging
from collections import defaultdict

import psycopg2
from odoo import _, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def _connect_to_db(host, port, user, password, dbname):
    try:
        conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
        return conn
    except psycopg2.Error:
        return None


class AccountConnectDB(models.Model):
    _name = 'account.connect.db'
    _description = 'Account Connect DB'

    db_source_name = fields.Char(string='DB Source Name', required=True)
    db_source_host = fields.Char(string='DB Source Host', required=True)
    db_source_port = fields.Char(string='DB Source Port', required=True)
    db_source_user = fields.Char(string='DB Source User', required=True)
    db_source_password = fields.Char(string='DB Source Password', required=True)
    db_target_name = fields.Char(string='DB Target Name', required=True)
    db_target_host = fields.Char(string='DB Target Host', required=True)
    db_target_port = fields.Char(string='DB Target Port', required=True)
    db_target_user = fields.Char(string='DB Target User', required=True)
    db_target_password = fields.Char(string='DB Target Password', required=True)

    def check_connection(self):
        # connect to source db
        source_db = _connect_to_db(self.db_source_host, self.db_source_port, self.db_source_user,
                                   self.db_source_password, self.db_source_name)

        target_db = _connect_to_db(self.db_target_host, self.db_target_port, self.db_target_user,
                                   self.db_target_password, self.db_target_name)
        print(source_db,target_db)
        return source_db, target_db

    def create_record(self, source_model, vals):
        new_account = self.env[source_model].sudo().create(vals)
        return new_account

    def model_mapping_id(self, model_id):
        model_mapping = self.env['model.mapping'].search([('model_id', '=', model_id)])
        existing_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                            model_mapping.read(['source_db_id', 'target_db_id'])}
        return existing_mapping

    def move_data_from_source_table(self, source_table):

        source_model = source_table.replace('_', '.')

        try:
            # Connect to source and target databases
            source_db, target_db = self.check_connection()
            existing_mapping = self.model_mapping_id(source_model)

            # Drop Constrains
            if source_table == 'account_journal':
                # # Delete all records from model_mapping
                # self.env['model.mapping'].with_context(force_delete=True).search([
                #     ('model_id', '=', source_model)
                # ]).unlink()
                #
                # # Delete all records from account.journal
                # journals = self.env['account.journal'].with_context(force_delete=True).search([])
                # if journals:
                #     journals.unlink()

                # Drop foreign key constraint on 'alias_id'
                try:
                    target_db_cur = target_db.cursor()
                    target_db_cur.execute(
                        """ALTER TABLE account_journal DROP CONSTRAINT IF EXISTS account_journal_alias_id_fkey;""")
                    target_db_cur.execute(
                        """ALTER TABLE account_journal DROP CONSTRAINT IF EXISTS account_journal_code_company_uniq;""")
                    target_db.commit()
                    _logger.info(
                        "Dropped foreign key constraint 'account_journal_alias_id_fkey' and unique constraint 'account_journal_code_company_uniq' from 'account_journal'.")
                except Exception as e:
                    _logger.error(
                        f"Failed to drop constraints 'account_journal_alias_id_fkey' and 'account_journal_code_company_uniq': {e}")
                    target_db.rollback()

            # Prepare to fetch data from source database
            source_db_cur = source_db.cursor()
            source_db_cur.execute(f"SELECT * FROM {source_table} LIMIT 0")
            source_columns = [desc[0] for desc in source_db_cur.description or []]
            shared_fields = [
                field for field in self.env[source_model]._fields
                if field in source_columns and field != 'id'
            ]
            source_db_cur.execute(f"SELECT * FROM {source_table}")
            source_records = source_db_cur.fetchall()
            for record in source_records:
                source_id = record[source_columns.index('id')]

                # Skip if the record already exists in the mapping
                if str(source_id) in existing_mapping:
                    print(f"Record {source_id} already exists.")
                    continue

                # Prepare values for creating a new record
                record_values = {f: record[source_columns.index(f)] for f in shared_fields}
                if source_table == 'account_journal':
                    account_mapping = self.model_mapping_id('account.account')
                    record_values['alias_id'] = None
                    if record_values['profit_account_id']:
                        print(record_values['profit_account_id'])
                        record_values['profit_account_id'] = int(
                            account_mapping[str(record_values['profit_account_id'])])
                    if record_values['loss_account_id']:
                        record_values['loss_account_id'] = int(account_mapping[str(record_values['loss_account_id'])])
                    if record[source_columns.index('default_debit_account_id')]:
                        record_values['default_account_id'] = int(
                            account_mapping[str(record[source_columns.index('default_debit_account_id')])])

                if source_table == 'account_account':
                    # set the correct type
                    # 1- find the name of type in user_type_id
                    user_type_id = record[source_columns.index('user_type_id')]
                    source_db_cur.execute(f"SELECT name FROM account_account_type where id={user_type_id}")
                    user_type_name = source_db_cur.fetchall()[0][0]

                    # 2- mapping the account record from source to target
                    account_type_selection = dict((v, k) for k, v in
                                                  self.env['account.account'].fields_get(allfields=['account_type'])[
                                                      'account_type']['selection'])
                    record_values['account_type'] = account_type_selection[user_type_name]

                # Create the record in the target database
                try:
                    print("first try")
                    # Start of the transaction
                    target_db_cur = target_db.cursor()
                    try:
                        print('second try')
                        # print(f"insert into {source_table} ({', '.join(record_values.keys())}) VALUES ({', '.join(['%s'] * len(record_values))})",
                        #     list(record_values.values()))
                        target_db_cur.execute(f"""
                            INSERT INTO account_journal (
                                message_main_attachment_id, name, code, active, type, restrict_mode_hash_table, sequence,
                                invoice_reference_type, invoice_reference_model, currency_id, company_id, refund_sequence,
                                profit_account_id, loss_account_id, bank_account_id, bank_statements_source, alias_id,
                                secure_sequence_id, create_uid, create_date, write_uid, write_date, show_on_dashboard,
                                color, default_account_id
                            ) VALUES (
                                NULL, 'Customer Invoices', 'INV', TRUE, 'sale', NULL, 5,
                                'invoice', 'odoo', NULL, 1, TRUE,
                                NULL, NULL, NULL, 'undefined', NULL,
                                NULL, 1, '2024-12-15 09:49:02.824694', 1, '2024-12-15 09:49:02.824694', TRUE,
                                11, 439
                            ) RETURNING id;
                        """)
                        new_account_id = target_db_cur.fetchone()[0]
                        target_db.commit()
                        print("done",new_account_id)
                    except Exception:
                        print("error")
                        target_db.rollback()
                        raise Exception
                    try:
                        print('third try')
                        # Safely create the mapping
                        self.env['model.mapping'].create({
                            'model_id': source_model,
                            'source_db_id': source_id,
                            'target_db_id': new_account_id
                        })
                        print("hello")
                    except Exception as map_error:
                        _logger.error(f"Mapping failed for record {source_id}: {str(map_error)}")
                        continue

                    # Success: both the record and mapping were created
                    print("Record and mapping created successfully")

                except Exception as create_error:
                    _logger.error(f"Error creating record {source_id}: {str(create_error)}")

        except Exception as e:
            raise ValidationError(_("Error during account account migration: %s") % str(e))

    def action_check_connection(self):
        # connect to source db
        source_db, target_db = self.check_connection()
        if source_db:
            print("Connected to source db")
        else:
            print("Error connecting to source db")

        # connect to target db
        if target_db:
            print("Connected to target db")
        else:
            print("Error connecting to target db")

    def action_migrate_account_move_data(self):
        try:
            # Connect to source and target databases
            source_db, target_db = self.check_connection()
        except Exception as e:
            raise ValidationError(_("Error connecting to databases: %s") % str(e))

        source_db_cur = source_db.cursor()

        # Get model mapping
        model_mapping = self.env['model.mapping'].search([('model_id', '=', 'account.move')])
        existing_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                            model_mapping.read(['source_db_id', 'target_db_id'])}

        try:
            # Fetch account_move structure and shared fields
            source_db_cur.execute("SELECT * FROM account_move LIMIT 0")
            source_columns = [desc[0] for desc in source_db_cur.description or []]
            shared_fields = [
                field for field in self.env['account.move']._fields
                if field in source_columns and field != 'id'
            ]

            # Fetch all account_move records
            source_db_cur.execute("SELECT * FROM account_move")
            account_moves = source_db_cur.fetchall()

            new_ids = defaultdict()

            for record in account_moves:
                source_id = record[source_columns.index('id')]

                if source_id in existing_mapping:
                    print("Skipping account_move ID %s, already migrated.", source_id)
                    new_ids[source_id] = existing_mapping[source_id]
                    continue

                try:
                    # Prepare account.move values
                    move_values = {f: record[source_columns.index(f)] for f in shared_fields}
                    move_values['move_type'] = record[source_columns.index('type')]
                    move_values['auto_post'] = 'yes' if record[source_columns.index('auto_post')] else 'no'

                    # map journal_id
                    journal_model_mapping = self.model_mapping_id('account.journal')
                    print(move_values)
                    print(journal_model_mapping)
                    if move_values['journal_id']:
                        print(int(journal_model_mapping[move_values['journal_id']]))
                        move_values['journal_id'] = journal_model_mapping[move_values['journal_id']]
                    # Create account.move record
                    move = self.env['account.move'].create(move_values)
                    new_ids[source_id] = move.id

                    # Create mapping for the new record
                    self.env['model.mapping'].create({
                        'model_id': 'account.move',
                        'source_db_id': source_id,
                        'target_db_id': move.id
                    })

                    _logger.info("Successfully migrated account_move ID %s to %s.", source_id, move.id)

                except Exception as e:
                    _logger.error("Error migrating account_move ID %s: %s", source_id, str(e))
                    continue

        finally:
            # Ensure cursor and connection cleanup
            source_db_cur.close()
            source_db.close()
            target_db.close()

    def action_migrate_account_move_line_data(self):
        # Get model mapping
        model_mapping_account_move_line = self.env['model.mapping'].search([('model_id', '=', 'account.move.line')])
        model_mapping_account_move = self.env['model.mapping'].search([('model_id', '=', 'account.move')])
        account_account  = self.env['model.mapping'].search([('model_id', '=', 'account.account')])
        account_journal  = self.env['model.mapping'].search([('model_id', '=', 'account.journal')])
        existing_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                            model_mapping_account_move_line.read(['source_db_id', 'target_db_id'])}
        account_move_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                                model_mapping_account_move.read(['source_db_id', 'target_db_id'])}
        account_account_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                                account_account.read(['source_db_id', 'target_db_id'])}
        account_journal_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                                   account_journal.read(['source_db_id', 'target_db_id'])}

        try:
            # Connect to source and target databases
            source_db, target_db = self.check_connection()
        except Exception as e:
            raise ValidationError(_("Error connecting to databases: %s") % str(e))

        # Prepare to fetch data from source database
        source_db_cur = source_db.cursor()

        try:
            source_db_cur.execute("SELECT * FROM account_move_line LIMIT 0")
            source_columns = [desc[0] for desc in source_db_cur.description or []]
            shared_fields = [
                field for field in self.env['account.move.line']._fields
                if field in source_columns and field != 'id'
            ]

            source_db_cur.execute("SELECT * FROM account_move_line")
            source_records = source_db_cur.fetchall()
            for record in source_records:
                source_id = record[source_columns.index('id')]
                move_id = record[source_columns.index('move_id')]
                # Skip if the record already exists in the mapping
                if source_id in existing_mapping:
                    print(f"Record {source_id} already exists.")
                    continue

                # Prepare values for creating a new record
                try:
                    move_line_values = {f: record[source_columns.index(f)] for f in shared_fields}
                    move_line_values['move_id'] = int(account_move_mapping[move_id])
                    move_line_values['currency_id'] = move_line_values['company_currency_id']
                    move_line_values['account_id'] = account_account_mapping[move_line_values['account_id']]
                    print(account_account_mapping)
                    # move_line_values['journal_id'] = account_journal_mapping[move_line_values['journal_id']]
                    print(move_line_values)
                    # Create the record in the target database
                    new_account_move_line = self.env['account.move.line'].create(move_line_values)
                    self.env['model.mapping'].create({
                        'model_id': 'account.move.line',
                        'source_db_id': source_id,
                        'target_db_id': new_account_move_line.id
                    })
                except Exception as e:
                    # Log the error and skip the problematic record
                    print(f"Error processing record {source_id}: {str(e)}")
                    continue
        except Exception as e:
            print("aaaaaaaaaaaa")

    def action_migrate_account_customer_data(self):
        # Get the model mapping
        model_mapping = self.env['model.mapping'].search([('model_id', '=', 'res.partner')])
        existing_mapping = {rec['source_db_id']: rec['target_db_id'] for rec in
                            model_mapping.read(['source_db_id', 'target_db_id'])}

        try:
            # Connect to source and target databases
            source_db, target_db = self.check_connection()
        except Exception as e:
            raise ValidationError(_("Error connecting to databases: %s") % str(e))

        # Prepare to fetch data from source database
        source_db_cur = source_db.cursor()

        try:
            source_db_cur.execute("SELECT * FROM res_partner LIMIT 0")
            source_columns = [desc[0] for desc in source_db_cur.description or []]
            shared_fields = [
                field for field in self.env['res.partner']._fields
                if field in source_columns and field != 'id'
            ]

            source_db_cur.execute("SELECT * FROM res_partner")
            source_records = source_db_cur.fetchall()
            print(existing_mapping)
            for record in source_records:
                source_id = record[source_columns.index('id')]

                # Skip if the record already exists in the mapping
                if str(source_id) in existing_mapping:
                    print(f"Record {source_id} already exists.")
                    continue

                # Prepare values for creating a new record
                try:
                    customer_values = {f: record[source_columns.index(f)] for f in shared_fields}
                    customer_values['commercial_partner_id'] = None

                    # Create the record in the target database
                    new_partner = self.env['res.partner'].create(customer_values)
                    self.env['model.mapping'].create({
                        'model_id': 'res.partner',
                        'source_db_id': source_id,
                        'target_db_id': new_partner.id
                    })
                except Exception as e:
                    # Log the error and skip the problematic record
                    print(f"Error processing record {source_id}: {str(e)}")
                    continue
        finally:
            # Ensure cursors and connections are closed
            source_db_cur.close()
            source_db.close()
            target_db.close()

    def action_migrate_account_account(self):
        self.env['model.mapping'].move_data_from_source_table("account_account")
        self.env['model.mapping'].move_data_from_source_table('account_journal')


    def action_migrate_product_product(self):
        # self.env['model.mapping'].move_data_from_source_table("product_category")
        # self.env['model.mapping'].move_data_from_source_table("product_attribute")
        # self.env['model.mapping'].move_data_from_source_table("product_attribute_value")
        # self.env['model.mapping'].move_data_from_source_table("product_template_attribute_line")
        self.env['model.mapping'].move_data_from_source_table("product_template")

        # self.env['model.mapping'].move_data_from_source_many_to_many_table('product_attribute_value_product_template_attribute_line_rel')
        # self.env['model.mapping'].move_data_from_source_table("product_attribute_product_template_rel")



