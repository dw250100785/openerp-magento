# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (c) 2011 Zikzakmedia S.L. (http://zikzakmedia.com) All Rights Reserved.
#                       Raimon Esteve <resteve@zikzakmedia.com>
#    $Id$
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from osv import osv, fields
from tools.translate import _

class res_partner(osv.osv):
    _inherit = "res.partner"

    _columns = {
        'magento_app_customer': fields.one2many('magento.app.customer', 'partner_id', 'Magento Customer'),
    }

res_partner()

class res_partner_address(osv.osv):
    _inherit = "res.partner.address"

    _columns = {
        'magento_firstname':fields.char('First Name', size=100),
        'magento_lastname':fields.char('Last Name', size=100),
    }

res_partner_address()
