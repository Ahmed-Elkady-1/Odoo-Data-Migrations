# -*- coding: utf-8 -*-
{
    'name': 'odoo_data_migrations',
    'category': 'Tools',
    'summary': 'Migrate data from old database to new one',
    'version': '1.0',
    'depends': ['base'],
    'data': [
        # secirity
        'security/ir.model.access.csv',

        # views
        'views/account_connection_views.xml',
        'views/account_model_analysis_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
