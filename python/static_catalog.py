from pymongo import MongoClient

# Make a version of the Radio Galaxy Zoo catalog that's perusable as a flat FITS or CSV table. 
# This is based on the output of:
#   consensus.py
#   RGZcatalog.py

'''
Set the desired consensus level of the RGZ classifications for this output (between 0 and 1).
0 will include every source in the catalog; 1 would include only those with 100% consensus. 
Default right now is (arbitrarily) set at 50%. One could also output the entire catalog
with consensus_level = 0 and then perform cuts later in their analysis, since consensus_level
is included as an output parameter.
'''

from consensus import rgz_path
consensus_level = 0.0

# Define a suffix that will be appended to the filename of the new catalog output.

if consensus_level == 0.:
    suffix = '_full'
else:
    suffix = ''

def load_data():

    # Load the matched catalog from MongoDB

    client = MongoClient('localhost', 27017)
    db = client['radio'] 
    
    catalog = db['weighted_catalog_dr1']

    return catalog

def flat_version(catalog):

    # Write the MongoDB catalog to a CSV file, with the fields described here:
    # http://radiogalaxyzoo.pbworks.com/w/page/108921379/DR1%20testing

    filename = '%s/csv/static_rgz_flat%s.csv' % (rgz_path,suffix)

    with open(filename,'w') as f:

        # Manually order fields
        fields = ['catalog_id', 'rgz_name', 'zooniverse_id', 'atlas_id', \
                  'radio.ra', 'radio.dec', 'consensus.ir_ra', 'consensus.ir_dec', 'consensus.ir_flag', 'consensus.n_total', 'consensus.n_radio', \
                      'consensus.n_ir', 'consensus.radio_level', 'consensus.ir_level', 'radio.number_components', 'radio.number_peaks', \
                      'radio.max_angular_extent', 'radio.total_solid_angle', 'radio.outermost_level', 'radio.max_physical_extent', 'radio.total_cross_section', \
                  'component.peak_fluxes', 'component.peak_flux_errs', 'component.peak_ras', 'component.peak_decs', \
                  'peak.fluxes', 'peak.flux_errs', 'peak.ras', 'peak.decs', \
                  'radio.total_flux', 'radio.total_flux_err', 'radio.total_luminosity', 'radio.total_luminosity_err', \
                  'SWIRE.designation', 'SWIRE.ra', 'SWIRE.dec', \
                      'SWIRE.flux_ap2_36', 'SWIRE.flux_ap2_45', 'SWIRE.flux_ap2_58', 'SWIRE.flux_ap2_80', \
                      'SWIRE.number_matches', \
                  'duplicate_sources.share_components', 'duplicate_sources.match_components', 'duplicate_sources.WISE_cat_mismatch']
        header = ''
        for field in fields:
            header += '{},'.format(field)
        header = header[:-1]

        print >> f,header
        good_entry,bad_entry = 0,0

        # Find all duplicate sources for deletion
        cids_for_removal = []
        for c in catalog.find({'duplicate_sources.exact_duplicate':{'$exists':True}}):
            if c['catalog_id'] != min(c['duplicate_sources']['exact_duplicate']):
                cids_for_removal.append(c['catalog_id'])
        
        # Select all matching galaxies (in this case, sources with optical and IR counterparts)
        args = {'catalog_id':{'$nin':cids_for_removal},'consensus.radio_level':{'$gte':consensus_level}}#,'SDSS':{'$exists':True},'AllWISE':{'$exists':True}}
        
        # Loop over number of RGZ catalog entries that match the consensus requirements
        for c in catalog.find(args).sort([('catalog_id', 1)]):

            # Determine component strings
            component_strings = {'peak_fluxes':'', 'peak_flux_errs':'', 'peak_ras':'', 'peak_decs':''}
            for component in c['radio']['components']:
                maxPeak = {'flux':-99, 'ra':-99, 'dec':-99}
                for peak in c['radio']['peaks']:
                    if component['ra_range'][0] <= peak['ra'] <= component['ra_range'][1] and \
                       component['dec_range'][0] <= peak['dec'] <= component['dec_range'][1] and \
                       peak['flux'] > maxPeak['flux']:
                        maxPeak = peak.copy()
                component_strings['peak_fluxes'] += '{};'.format(maxPeak['flux'])
                component_strings['peak_flux_errs'] += '{};'.format(c['radio']['peak_flux_err'])
                component_strings['peak_ras'] += '{};'.format(maxPeak['ra'])
                component_strings['peak_decs'] += '{};'.format(maxPeak['dec'])
            for key in component_strings:
                component_strings[key] = '"{}"'.format(component_strings[key][:-1])

            # Determine peak strings (only for single component sources)
            peak_strings = {'fluxes':'', 'flux_errs':'', 'ras':'', 'decs':''}
            if c['radio']['number_components'] == 1:
                for peak in c['radio']['peaks']:
                    peak_strings['fluxes'] += '{};'.format(peak['flux'])
                    peak_strings['flux_errs'] += '{};'.format(c['radio']['peak_flux_err'])
                    peak_strings['ras'] += '{};'.format(peak['ra'])
                    peak_strings['decs'] += '{};'.format(peak['dec'])
                for key in peak_strings:
                    peak_strings[key] = '"{}"'.format(peak_strings[key][:-1])
            else:
                for key in peak_strings:
                    peak_strings[key] = -99

            # Determine overlap strings (when applicable)
            duplicate_strings = {'share_components':'', 'match_components':'', 'WISE_cat_mismatch':''}
            if 'duplicate_sources' in c:
                for key in duplicate_strings:
                    if key in c['duplicate_sources']:
                        for cid in c['duplicate_sources'][key]:
                            if cid not in cids_for_removal:
                                duplicate_strings[key] += '{};'.format(cid)
                        duplicate_strings[key] = '"{}"'.format(duplicate_strings[key][:-1])
            for key in duplicate_strings:
                if (duplicate_strings[key] == '') or (duplicate_strings[key] == '""'):
                    duplicate_strings[key] = -99

            # Combine sources (if duplicates exist)
            if 'duplicate_sources' in c and 'exact_duplicate' in c['duplicate_sources']:
                n_total, n_radio, n_ir = 0, 0, 0
                ir_ra, ir_dec = 0., 0.
                for d in catalog.find({'catalog_id': {'$in': c['duplicate_sources']['exact_duplicate']}}):
                    n_total += d['consensus']['n_total']
                    n_radio += d['consensus']['n_radio']
                    n_ir += d['consensus']['n_ir']
                    if 'ir_ra' in d['consensus']:
                        ir_ra += d['consensus']['n_ir'] * d['consensus']['ir_ra']
                        ir_dec += d['consensus']['n_ir'] * d['consensus']['ir_dec']
                if not n_ir:
                   import logging
                   logging.warning('Skipping no IR {}'.format(c))
                   continue
                ir_ra /= n_ir
                ir_dec /= n_ir
                c['consensus']['n_total'] = n_total
                c['consensus']['n_radio'] = n_radio
                c['consensus']['n_ir'] = n_ir
                c['consensus']['radio_level'] = 1.0*n_radio/n_total
                c['consensus']['ir_level'] = 1.0*n_ir/n_radio
                c['consensus']['ir_flag'] = 0
                if ir_ra:
                    c['consensus']['ir_ra'] = ir_ra
                    c['consensus']['ir_dec'] = ir_dec

            # Print all values to new row in file.
            try:
                row = []
                for field in fields:
                    if '.' in field:
                        prefix, field = field.split('.')
                        if prefix == 'component':
                            row.append(component_strings[field])
                        elif prefix == 'peak':
                            row.append(peak_strings[field])
                        elif prefix == 'duplicate_sources':
                            row.append(duplicate_strings[field])
                        elif prefix in ['AllWISE', 'SDSS'] and prefix not in c and field == 'number_matches':
                            row.append(0)
                        elif prefix in c and field in c[prefix]:
                            row.append(c[prefix][field])
                        else:
                            row.append(-99)
                    else:
                        row.append(c[field])

                prow = [str(x) for x in row]
                print >> f,','.join(prow)
                good_entry += 1

            except IndexError:
                # If couldn't find one or more of the fields selected
                bad_entry += 1
                print "Unable to print {0}".format(c['catalog_id'])

        # Print summary to screen

        print "{0:d} duplicate sources removed".format(len(cids_for_removal))
        print "{0:d} entries written to CSV file {1}".format(good_entry,filename)
        print "{0:d}/{1:d} had errors writing data to file".format(bad_entry,catalog.find(args).count())

    return None

def paired_version(catalog):

    # Write the MongoDB catalog to two CSV files:
    # one that contains host information, and one that contains component information
    # http://radiogalaxyzoo.pbworks.com/w/page/108921379/DR1%20testing

    host_filename = '%s/csv/static_rgz_host%s.csv' % (rgz_path,suffix)
    component_filename = '%s/csv/static_rgz_component%s.csv' % (rgz_path,suffix)
    comp_count = 0

    with open(host_filename,'w') as hf:

        with open(component_filename,'w') as cf:

            h_fields = ['catalog_id', 'rgz_name', 'zooniverse_id', 'atlas_id', \
                        'radio.ra', 'radio.dec', 'consensus.ir_ra', 'consensus.ir_dec', 'consensus.ir_flag', 'consensus.n_total', 'consensus.n_radio', \
                            'consensus.n_ir', 'consensus.radio_level', 'consensus.ir_level', 'radio.number_components', 'radio.number_peaks', \
                            'radio.max_angular_extent', 'radio.total_solid_angle', 'radio.outermost_level', 'radio.max_physical_extent', 'radio.total_cross_section', \
                        'radio.total_flux', 'radio.total_flux_err', 'radio.total_luminosity', 'radio.total_luminosity_err', \
                        'SWIRE.designation', 'SWIRE.ra', 'SWIRE.dec', \
                            'SWIRE.flux_ap2_36', 'SWIRE.flux_ap2_45', 'SWIRE.flux_ap2_58', 'SWIRE.flux_ap2_80', \
                            'SWIRE.number_matches', \
                        'duplicate_sources.share_components', 'duplicate_sources.match_components', 'duplicate_sources.WISE_cat_mismatch']

            h_header = ''
            for field in h_fields:
                h_header += '{},'.format(field)
            h_header = h_header[:-1]

            print >> hf,h_header
            good_entry_h,bad_entry_h = 0,0

            c_fields = ['catalog_id', 'ra', 'dec', 'flux', 'flux_err', 'peak_flux', 'peak_flux_err', 'angular_extent', 'solid_angle', \
                        'physical_extent', 'cross_section']

            c_header = ''
            for field in c_fields:
                c_header += '{},'.format(field)
            c_header = c_header[:-1]

            print >> cf,c_header
            good_entry_c,bad_entry_c = 0,0

            # Find all duplicate sources for deletion
            cids_for_removal = []
            for c in catalog.find({'duplicate_sources.exact_duplicate':{'$exists':True}}):
                if c['catalog_id'] != min(c['duplicate_sources']['exact_duplicate']):
                    cids_for_removal.append(c['catalog_id'])
            
            # Select all matching galaxies (in this case, sources with optical and IR counterparts)
            args = {'catalog_id':{'$nin':cids_for_removal},'consensus.radio_level':{'$gte':consensus_level}}#,'SDSS':{'$exists':True},'AllWISE':{'$exists':True}}

            # Loop over number of RGZ catalog entries that match the consensus requirements
            for c in catalog.find(args).sort([('catalog_id', 1)]):

                # Determine overlap strings (when applicable)
                duplicate_strings = {'share_components':'', 'match_components':'', 'WISE_cat_mismatch':''}
                if 'duplicate_sources' in c:
                    for key in duplicate_strings:
                        if key in c['duplicate_sources']:
                            for cid in c['duplicate_sources'][key]:
                                if cid not in cids_for_removal:
                                    duplicate_strings[key] += '{};'.format(cid)
                            duplicate_strings[key] = '"{}"'.format(duplicate_strings[key][:-1])
                for key in duplicate_strings:
                    if (duplicate_strings[key] == '') or (duplicate_strings[key] == '""'):
                        duplicate_strings[key] = -99

                # Combine sources (if duplicates exist)
                if 'duplicate_sources' in c and 'exact_duplicate' in c['duplicate_sources']:
                    n_total, n_radio, n_ir = 0, 0, 0
                    ir_ra, ir_dec = 0., 0.
                    for d in catalog.find({'catalog_id': {'$in': c['duplicate_sources']['exact_duplicate']}}):
                        n_total += d['consensus']['n_total']
                        n_radio += d['consensus']['n_radio']
                        n_ir += d['consensus']['n_ir']
                        if 'ir_ra' in d['consensus']:
                            ir_ra += d['consensus']['n_ir'] * d['consensus']['ir_ra']
                            ir_dec += d['consensus']['n_ir'] * d['consensus']['ir_dec']
                    if not n_ir:
                        import logging
                        logging.warning('Skipping no IR: {}'.format(c))
                        continue
                    ir_ra /= n_ir
                    ir_dec /= n_ir
                    c['consensus']['n_total'] = n_total
                    c['consensus']['n_radio'] = n_radio
                    c['consensus']['n_ir'] = n_ir
                    c['consensus']['radio_level'] = 1.0*n_radio/n_total
                    c['consensus']['ir_level'] = 1.0*n_ir/n_radio
                    c['consensus']['ir_flag'] = 0
                    if ir_ra:
                        c['consensus']['ir_ra'] = ir_ra
                        c['consensus']['ir_dec'] = ir_dec

                # Print values to new row in host file
                try:
                    row = []
                    for field in h_fields:
                        if '.' in field:
                            prefix, field = field.split('.')
                            if prefix == 'duplicate_sources':
                                row.append(duplicate_strings[field])
                            elif prefix in ['AllWISE', 'SDSS'] and prefix not in c and field == 'number_matches':
                                row.append(0)
                            elif prefix in c and field in c[prefix]:
                                row.append(c[prefix][field])
                            else:
                                row.append(-99)
                        else:
                            row.append(c[field])

                    prow = [str(x) for x in row]
                    print >> hf,','.join(prow)
                    good_entry_h += 1

                except IndexError:
                    # If couldn't find one or more of the fields selected
                    bad_entry_h += 1
                    print "Unable to print {0} to host file".format(c['catalog_id'])

                # Iterate over each component for component table
                for comp in c['radio']['components']:

                    comp_count += 1

                    #Find the peak on the component, unfortunately not marked in Mongo
                    peak = {'ra':-99, 'dec':-99, 'flux':-99}
                    for p in c['radio']['peaks']:
                        if (comp['ra_range'][0] <= p['ra'] <= comp['ra_range'][1]) and (comp['dec_range'][0] <= p['dec'] <= comp['dec_range'][1]) \
                           and p['flux'] > peak['flux']:
                            peak = p

                    # Print values to new row in component file
                    try:
                        row = []
                        for field in c_fields:
                            if field == 'catalog_id':
                                row.append(c['catalog_id'])
                            elif field in ['ra', 'dec']:
                                row.append(peak[field])
                            elif field == 'peak_flux':
                                row.append(peak['flux'])
                            elif field == 'peak_flux_err':
                                row.append(c['radio']['peak_flux_err'])
                            elif field in comp:
                                row.append(comp[field])
                            else:
                                row.append(-99)

                        prow = [str(x) for x in row]
                        print >> cf,','.join(prow)
                        good_entry_c += 1

                    except IndexError:
                        # If couldn't find one or more of the fields selected
                        bad_entry_c += 1
                        print "Unable to print {0} to component file".format(c['catalog_id'])
                    
            # Print summary to screen

            print "{0:d} duplicate sources removed".format(len(cids_for_removal))
            print "{0:d} entries written to host CSV file {1}".format(good_entry_h,host_filename)
            print "{0:d}/{1:d} had errors writing data to host file".format(bad_entry_h,catalog.find(args).count())
            print "{0:d} entries written to component CSV file {1}".format(good_entry_c,component_filename)
            print "{0:d}/{1:d} had errors writing data to component file".format(bad_entry_c,comp_count)

    return None

# Hasn't been updated with new fields
##def selected_fields(catalog,full=False):
##
##    # Write the MongoDB catalog to a CSV file, but only include specific fields.
##
##    filename = '%s/csv/static_rgz_selected%s.csv' % (path,suffix)
##
##    # Check the WISE and SDSS fields to see if they had a match; if not, return null values
##
##    wise_default_dict = catalog.find_one({'AllWISE':{'$exists':True}})['AllWISE']
##    for k in wise_default_dict:
##        wise_default_dict[k] = -99.
##    wise_default_dict['designation'] = 'no_wise_match'
##    wise_default_dict['numberMatches'] = 0
##
##    sdss_default_dict = catalog.find_one({'SDSS':{'$exists':True},'SDSS.redshift':{'$exists':False}})['SDSS']
##    for k in sdss_default_dict:
##        sdss_default_dict[k] = -99.
##    sdss_default_dict['objID'] = 'no_sdss_match'
##    sdss_default_dict['numberMatches'] = 0
##
##    with open(filename,'w') as f:
##
##        # CSV file header
##        print >> f,'source_id,zooniverse_id,n_radio,ra_min,ra_max,dec_min,dec_max,max_angular_extent,total_solid_angle,wise_id,wise_ra,wise_dec,wise_w1mag,redshift,redshift_err,redshift_type,sdss_id,sdss_ra,sdss_dec,consensus_level'
##        good_entry,bad_entry = 0,0
##
##        # Select all matching galaxies (in this case, double-lobed sources with optical and IR counterparts)
##        args = {'consensus.level':{"$gte":consensus_level},'SDSS':{'$exists':True},'AllWISE':{'$exists':True}}
##
##        # Loop over number of RGZ catalog entries that match the consensus requirements
##        for c in catalog.find(args):
##            wiseval = c.setdefault('AllWISE',wise_default_dict)
##            sdssval = c.setdefault('SDSS',sdss_default_dict)
##            sdssredshift = c['SDSS'].setdefault('redshift',-99.)
##            sdssredshifterr = c['SDSS'].setdefault('redshift_err',-99.)
##            sdssredshifttype = c['SDSS'].setdefault('redshift_type',-99)
##
##            # Find the maximum RA, dec extent on the radio components
##
##            components = c['radio']['components']
##            ra_min,ra_max = components[0]['raRange']
##            dec_min,dec_max = components[0]['decRange']
##            for comp in components:
##                ra_min = ra_min if comp['raRange'][0] > ra_min else comp['raRange'][0]
##                ra_max = ra_max if comp['raRange'][1] < ra_max else comp['raRange'][1]
##                dec_min = dec_min if comp['decRange'][0] > dec_min else comp['decRange'][0]
##                dec_max = dec_max if comp['decRange'][1] < dec_max else comp['decRange'][1]
##
##            try:
##                # Print values to new row in file. -99 values for the first few roles can be removed if the fields are renumbered in this line.
##                print >> f,'RGZ_{0},{14},{21:d},{22:.5f},{23:.5f},{24:.5f},{25:.5f},{17:.3f},{18:.3f},{10},{7:.5f},{8:.5f},{9:.2f},{11:.4f},{12:.4f},{13:d},{16},{19:.5f},{20:.5f},{15:.2f}'.format(\
##                c['catalog_id'], 
##                -99,
##                -99,
##                -99,
##                -99,
##                -99,
##                -99,
##                c['AllWISE']['ra'],
##                c['AllWISE']['dec'],
##                c['AllWISE']['w1mpro'],
##                c['AllWISE']['designation'],
##                c['SDSS']['redshift'],
##                c['SDSS']['redshift_err'],
##                c['SDSS']['redshift_type'],
##                c['Zooniverse_id'],
##                c['consensus']['level'],
##                c['SDSS']['objID'],
##                c['radio']['maxAngularExtent'],
##                c['radio']['totalSolidAngle'],
##                c['SDSS']['ra'],
##                c['SDSS']['dec'],
##                c['radio']['numberComponents'],
##                ra_min,
##                ra_max,
##                dec_min,
##                dec_max)
##                good_entry += 1
##            except IndexError:
##                # If couldn't find one or more of the fields selected
##                bad_entry += 1
##
##        # Print summary to screen
##
##        print "{0:d} entries written to CSV file {1}".format(good_entry,filename)
##        print "{0:d}/{1:d} had errors writing data to file".format(bad_entry,catalog.find(args).count())
##
##    return None

if __name__ == "__main__":

    # Make the static catalog from the command line

    catalog = load_data()
    flat_version(catalog)
    paired_version(catalog)
