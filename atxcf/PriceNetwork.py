"""
PriceNetwork module. Maintains a graph representing an asset exchange network to determine
prices of assets relative to each other.
- transfix@sublevels.net - 20160117
"""
import PriceSource
import settings

from functools import partial
import networkx as nx

import string
import threading
import multiprocessing
import time


class PriceNetworkError(PriceSource.PriceSourceError):
    pass


class PriceNetwork(PriceSource.AllSources):

    def __init__(self, doInitGraph=False):
        super(PriceNetwork, self).__init__()
        self._price_graph = None

        # make sure there is a Baskets section in the settings
        sett = settings.get_settings()
        if not "Baskets" in sett:
            sett.update({"Baskets":{}})
        settings.set_settings(sett)

        if doInitGraph:
            self.init_graph()


    def _get_baskets(self):
        """
        Returns the dict of baskets
        """
        sett = settings.get_settings()
        return sett["Baskets"]


    def _get_basket_value(self, basket_name, to_asset, get_last=False):
        """
        Returns the basket's value in terms of to_asset.
        """
        baskets = self._get_baskets()
        if not basket_name in baskets:
            raise PriceNetworkError("%s: no such basket %s" % (self._class_name(), basket_name))
        basket_d = baskets[basket_name]
        to_asset_amount = 0.0
        for name, amount in basket_d.iteritems():
            to_asset_amount += self.get_price(name, to_asset, amount, get_last)
        return to_asset_amount


    def _is_basket(self, basket_name):
        """
        Returns boolean whether basket_name is a basket.
        """
        baskets = self._get_baskets()
        if basket_name in baskets:
            return True
        return False


    def _do_get_price(self, from_asset, to_asset, value=1.0, get_last=False):
        """
        Returns price of assets or baskets of assets.

        TODO: deal with possible cycles (baskets containing themselves...)
        """
        mkt_pair_str = "{0}/{1}".format(from_asset, to_asset)
        is_price_cached = self._has_stored_price(mkt_pair_str)
        interval = settings.get_option("price_update_interval")
        last_price = None

        if is_price_cached:
            when_cached = self._get_last_stored_price_time(mkt_pair_str)
            if get_last or time.time() - when_cached <= interval:
                last_price = self._get_stored_price(mkt_pair_str) * value

        if not last_price and self._is_basket(from_asset):
            return self._get_basket_value(from_asset, to_asset) * value

        if not last_price:
            last_price = super(PriceNetwork, self).get_price(from_asset, to_asset, value)

        return last_price


    def _generate_graph(self):
        all_symbols = self.get_symbols()
        
        # Create a graph linking all asset nodes with edges where
        # an exchange exists between them.
        G = nx.Graph()
        G.add_nodes_from(all_symbols)
        all_markets = self.get_markets()
        bad_markets = []
        good_markets = []

        def get_mkt_price(mkt_pair_str):
            mkt_pair = mkt_pair_str.split("/")
            try:
                last_price = None
                if self._has_stored_price(mkt_pair_str):
                    last_price = self._get_stored_price(mkt_pair_str)
                    print "Loading market", mkt_pair[0], mkt_pair[1]
                else:
                    last_price = self._do_get_price(mkt_pair[0], mkt_pair[1])
                    print "Adding market", mkt_pair[0], mkt_pair[1]
                return (mkt_pair[0], mkt_pair[1], last_price, "")
            except PriceSource.PriceSourceError as e:
                return (mkt_pair[0], mkt_pair[1], None, e.message)

        print "Polling known markets..."
        # multiproccessing isn't working... some pickling error
        #pool = multiprocessing.Pool()
        #all_market_prices = pool.map(get_mkt_price, all_markets, 32)
        all_market_prices = map(get_mkt_price, all_markets)
        error_msgs = []
        for from_mkt, to_mkt, last_price, msg in all_market_prices:
            if last_price == None:
                bad_markets.append("{0}/{1}".format(from_mkt, to_mkt))
                error_msgs.append("{0}/{1}: {2}".format(from_mkt, to_mkt, msg))
            else:
                G.add_edge(from_mkt, to_mkt, last_price = last_price)
                good_markets.append((from_mkt, to_mkt, last_price))

        # Markets available
        conv = []
        for item in good_markets:
            conv.append("{0}/{1}".format(item[0], item[1]))
        print "Known markets:", conv
        print "Number of markets:", len(conv)
        print "Number of symbols:", len(all_symbols)

        # There may have been errors retriving market info for some markets listed
        # as available. Let's print them out here.
        print "Dropped markets due to errors getting last price: ", error_msgs
        
        return G


    def _get_price_graph(self):
        with self._lock:
            if self._price_graph == None:
                self.init_graph()
            return self._price_graph


    def init_graph(self):
        """
        (Re-)generates the price network graph and assigns it to the _price_graph
        attrib.
        """
        with self._lock:
            self._price_graph = self._generate_graph()


    def get_baskets(self):
        """
        Returns a list of baskets.
        """
        return self._get_baskets().keys()


    def get_basket(self, basket_name):
        """
        Returns the contents of a basket.
        """
        baskets = self._get_baskets()
        if not basket_name in baskets:
            raise PriceNetworkError("No such basket_name %s" % basket_name)
        return baskets[basket_name]


    def get_symbols(self):
        # collect basket symbols
        baskets = self._get_baskets()
        basket_symbols = []
        for basket_name in baskets.keys():
            basket_symbols.append(basket_name)
        # then add them to whatever AllSources gives us
        return basket_symbols + super(PriceNetwork, self).get_symbols()


    def set_basket(self, basket_name, basket_d):
        sett = settings.get_settings()
        sett["Baskets"].update({basket_name: basket_d})
        settings.set_settings(sett)
        self.init_graph() # re-init with new symbol info


    def remove_basket(self, basket_name):
        sett = settings.get_settings()
        del sett["Baskets"][basket_name]
        settings.set_settings(sett)
        self.init_graph() # re-init with new symbol info


    def get_markets(self):
        basket_markets = []
        baskets = self._get_baskets()
        for basket_name in baskets.keys():
            for base_cur in self.get_base_symbols():
                try:
                    trade_pair = "{0}/{1}".format(basket_name, base_cur)
                    self.get_price(basket_name, base_cur, 1.0, True) # check if this throws
                    basket_markets.append(trade_pair)
                except PriceSource.PriceSourceError:
                    pass
        return basket_markets + super(PriceNetwork, self).get_markets()


    def set_source(self, sourcename, source):
        init_sources([source])
        init_graph() # TODO: just add new edges


    def get_price(self, from_asset, to_asset, value = 1.0, get_last=False):
        
        # do nothing if they're the same
        if from_asset == to_asset:
            return value

        G = self._get_price_graph()
        sh_p = nx.shortest_path(G, from_asset, to_asset)
        if not sh_p or len(sh_p) <= 1:
            raise PriceNetworkError("No path from {0} to {1}"
                                    .format(from_asset, to_asset))
        # for each edge in the path, compute the conversion price
        cur_value = float(value)
        for from_cur, to_cur in zip(sh_p[0:], sh_p[1:]):
            cur_value = self._do_get_price(from_cur, to_cur, cur_value, get_last)
        return cur_value


    def price(self, trade_pair_str, value = 1.0, get_last=False):
        # trade_pair_str is a string with a slash separating two
        # asset symbols, like XBT/USD
        asset_strs = string.split(trade_pair_str,"/",1)
        if len(asset_strs) != 2:
            raise PriceNetworkError("Invalid trade_pair_str %s" % trade_pair_str)
        asset_strs = [cur.strip() for cur in asset_strs]
        return self.get_price(asset_strs[0], asset_strs[1], value, get_last)


_pn = None
def init():
    """
    (Re-)initializes the PriceNetwork singleton.
    """
    global _pn
    _pn = PriceNetwork()
    _pn.init_graph()


def _get_price_network():
    """
    Returns a singleton instance of a PriceNetwork.
    """
    global _pn
    if not _pn:
        init()
    return _pn
