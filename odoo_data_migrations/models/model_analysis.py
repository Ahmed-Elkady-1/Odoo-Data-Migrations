from networkx.algorithms.operators.binary import difference
from odoo import fields, models
from odoo.exceptions import UserError


class AccountModelAnalysis(models.Model):
    _name = 'account.model.analysis'
    _description = 'Account Model Analysis'

    model_name = fields.Char(string='Model Name')
    share_column = fields.Text(string='Share Column')
    additional_target_column = fields.Text(string='Additional Column In Target DB')
    info = fields.Text(string='Info')


    def action_show_account_move_difference(self):
        database_connection = self.env['account.connect.db'].browse(1)
        source_db, target_db = database_connection.check_connection()

        source_fields = self._get_fields_from_db(source_db, 'account_move')
        target_fields = self.env['account.move']._fields

        shared_fields = set(source_fields) & set(target_fields)
        additional_target_columns = set(target_fields) - set(source_fields)
        if self.search([('model_name', '=', 'account.move')], limit=1):
            raise UserError('You already have account.move model')
        self.write({
            'model_name': 'account.move',
            'share_column': ','.join(shared_fields),
            'additional_target_column': ','.join(additional_target_columns),
            'info': 'No info',
        })

    def _get_fields_from_db(self, db, table):
        db_cur = db.cursor()
        db_cur.execute("SELECT * FROM %s LIMIT 0" % table)
        return [desc[0] for desc in db_cur.description or []]